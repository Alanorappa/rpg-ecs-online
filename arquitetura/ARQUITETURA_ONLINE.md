# Arquitetura Online — Decisões e Referência

> Documento vivo. Atualizar sempre que uma decisão arquitetural for tomada.
> Última atualização: 2026-07-01 (cross-map AI fix; `_eid_to_map` eliminado — P3 completo; `player_entity_id` removido de AI systems; tiro_multiplo direction fix via `CAST_DIR_UPDATE`)

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

### 15. Catálogo de itens único — `item_table.py` (07/07/2026)

Antes desta data, itens eram definidos em até 3 lugares (`loot_tables.py`,
`merchant_data.py`, `crafting_data.py`) — 3 nomes ("Espada de Ferro", "Grevas
de Ferro", "Luvas de Couro") já tinham divergido de verdade (raridade/dano/
valor/mods diferentes dependendo de onde vinham comprados vs. dropados).

`item_table.py` agora é o catálogo único pra tudo que é loot ou vendido em
loja (`ITEMS` dict — 126 entradas). `loot_tables.py` mantém só as tabelas de
drop (`LOOT_TABLES`, `roll_loot`, `roll_mob_loot`) e reexporta `ITEMS` como
`_T` (compat — nada mais no código precisou mudar). `merchant_data.py` só
lista quais `item_table.ITEMS[...]` aparecem em cada loja e por quanto.
`crafting_data.py` fica de fora (materiais/receitas são exclusivos de
crafting, nunca duplicados em outro catálogo).

`Item` ganhou `item_level` (exibição, derivado de rarity+value via
`item_table._derive_item_level` — sem precisar tocar cada factory),
`level_requirement` (bloqueio real de equip — todo item em 1 por enquanto,
ajuste manual futuro) e `description` (texto livre opcional). Raridade
ganhou `legendary`/`mythic` (cor laranja/vermelho) em todos os mapas de cor
do cliente + `crafting_data.RARITY_RECYCLE_COST`/`RECYCLE_TABLE`.

**Bloqueio de equip server-autoritativo:** `update_player_equipment`
(`server/world_server.py`) agora valida `armor_class` (`CLASS_ARMOR_ALLOWED`)
e `level_requirement` por slot ANTES de aplicar — fechando uma brecha que só
existia como check client-side (`_equip_item`, nunca validado no servidor).
Slot que falha não é aplicado; servidor responde `EQUIP_REJECTED` (ver
tabela de mensagens) e o cliente reverte a UI otimista.

### 16. Sistema de trade (player↔player) — 08/07/2026

Shift+clique esquerdo num player remoto abre um mini-popup local (sem rede)
com botão "Trade"; aceitar/recusar convite e a janela de troca em si (5
slots + gold por lado, confirmar/cancelar) seguem o mesmo modelo WoW.
Autoridade total no servidor — `server/trade_processor.py`
(`TradeProcessorMixin` + `TradeSession`, sem ECS, mesmo nível de
bookkeeping que `_corpses`/`_mob_damage_log` em `WorldServer`).

**Custódia imediata:** item ofertado é REMOVIDO da `Inventory` real na hora
(não só "travado") e gold ofertado é debitado da `Wallet` na hora — fica em
custódia na `TradeSession` até confirmar (soma no destinatário) ou cancelar
(devolve ao dono original). Elimina de graça qualquer chance de vender/
equipar/usar o item ofertado durante o trade, sem checagem extra em nenhum
sistema existente. Cliente nunca muta a Inventory/Wallet local
otimisticamente — só reflete depois que `TRADE_STATE`/`TRADE_RESULT`/
`TRADE_CANCELLED` confirmam (mesmo racional do `EQUIP_REJECTED`), via
diff-por-nome (multiset, `collections.Counter`) entre a oferta antiga e a
nova em `client/network_handlers.py::_handle_msg_trade_state`.

**Distância máxima** (`TRADE_MAX_DIST_TILES`, `shared/constants.py`) checada
1×/tick (`WorldServer._tick_trade_distance_check`, chebyshev, mesmo mapa) —
sair do alcance cancela e devolve tudo (`reason="distance"`). Desconexão de
qualquer um dos dois lados durante um trade ativo também cancela e avisa o
outro (`server/session.py::on_disconnect`). Confirmar reseta ao MUDAR
qualquer oferta (item ou gold, de qualquer lado), igual WoW. Execução final
valida espaço de `Inventory` nos DOIS lados antes de aplicar — mochila cheia
recusa com `reason="inventory_full"` sem perder nada de ninguém.

Cliente: `client/trade_handlers.py` (mixin, popup/invite/janela),
`ui_components.TradeUIState` (componente ECS no player, mesmo padrão de
`ShopUIState`/`LootUIState`). Colocar item na oferta é clique DIREITO no
item da bag (não drag — `DragState` só cobre skill/consumível), mesmo
padrão já usado em `crafting_system.py::_handle_bag_rclick`.

**Layout da janela (08/07/2026, revisado por feedback visual):** a própria
bag (20 slots, 4×5) é renderizada DENTRO da janela de trade — não é mais um
painel de Inventory separado sobreposto (ficava impossível arrastar/ofertar
item com as duas janelas competindo pelo mesmo espaço). Ao lado da bag, 2
colunas de 5 slots alinhadas às linhas da bag: "minha oferta" (gold
editável num retângulo acima + itens ofertados, clique direito num slot
ofertado retira) e "oferta do outro" (read-only, mesmo formato). Cada
jogador só vê a PRÓPRIA bag — nunca a do outro, que só aparece via sua
coluna de oferta (já era assim no protocolo, `their_offer` nunca inclui a
bag inteira). Botão "Negociar" (confirma) + "Cancelar"; ao confirmar, uma
layer verde semi-transparente cobre a coluna de quem confirmou — dos dois
lados quando ambos confirmam.

### 17. Chat de texto — cliente implementado (08/07/2026)

O protocolo (`CHAT_SEND`/`CHAT_MESSAGE`) e o handler do servidor
(`server/session.py::_handle_chat`) já existiam de uma sessão anterior —
faltava o CLIENTE de verdade (campo pra digitar, log na tela, balão de
fala). Servidor continua 100% autoritativo e inalterado: valida
`channel` (`"local"` → AOI via `_broadcast_aoi_from_session`, `"world"` →
todos), corta o texto em 200 chars, e o AOI **sempre inclui o próprio
remetente** — por isso o cliente nunca ecoa a mensagem otimisticamente,
só reflete quando o `CHAT_MESSAGE` confirmado chega de volta (mesmo
racional de EQUIP_REJECTED/trade).

Cliente v1: Enter abre o campo, digita, Enter de novo manda `CHAT_SEND` com
`channel="local"` fixo (sem seletor de canal). Log era um único histórico
de 50 mensagens, sempre visível no canto inferior esquerdo. **Substituído
pelo redesign com abas (08/07/2026) descrito logo abaixo** — mantido aqui
só o histórico da decisão original.

**Achado no caminho — WASD escapa do filtro de `systems_events`:** o
filtro genérico que bloqueia KEYDOWN/clique dos sistemas ECS quando um
modal está aberto (`modal_stack_handlers.py`, reaproveitado aqui
registrando `"chat"` no registry) NÃO bloqueia movimento, porque
`PlayerInputSystem` lê `pygame.key.get_pressed()` direto (estado bruto
do teclado), não os eventos KEYDOWN da lista filtrada — digitar "w"/"a"/
"s"/"d" numa mensagem também moveria o personagem. Fix: `UIState` ganhou
`chat_active` (componente ECS, acessível de qualquer sistema sem
referência direta — mesmo racional de `show_inventory`/`show_talents`);
`PlayerInputSystem` checa esse campo e força `can_move = False` enquanto
o chat está focado. Balão de fala resolve o remetente pro eid local via
nome (`RemoteControlled.name` pra players remotos, `_logged_char_name`
pro próprio) — mobs/NPCs nunca mandam chat, então não precisam de match.

Validado (headless): suíte completa sem regressão (9F/83P — chat
server-side já existia e não foi alterado).

**Não validado:** passada manual com 2 clientes reais (campo de
digitação, balão de fala seguindo o personagem andando, WASD realmente
bloqueado durante a digitação).

### 18. Chat com abas (Local/Mundial/Combate) — redesign (08/07/2026)

A v1 (item 17) tinha só 1 canal e nenhum histórico de verdade. Redesenho
pra 3 abas — mesmo padrão visual de `client/debug_handlers.py`
(`self._chat_tab` + `self._chat_tab_buttons` construído no draw, checado
no clique): **Local** (AOI, igual antes), **Mundial** (o protocolo já
suportava `channel="world"` desde sempre — só nunca teve UI: aba nova
manda `CHAT_SEND` com esse canal), **Combate** (não é chat — é
`combat_log.py`, ver abaixo). Histórico de até **500 entradas POR ABA**
(3 deques independentes, não 1 total somado), scrollbar (track+thumb,
mesmo padrão de `LootSystem` em `systems.py`) e quebra de linha
(`ui_helpers.wrap_text`, já usada em tooltips).

**`combat_log.py` deixou de ser popup flutuante — virou histórico
persistente lido pela aba Combate.** Antes: `deque(maxlen=8)` com timer de
fade (8s + 2s), desenhado perto do HUD via `LOG.update(dt)`/`LOG.draw(...)`
(removidos de `game.py`). Agora: `deque(maxlen=500)`, sem timer/fade,
nova property `LOG.entries` (read-only) que a aba Combate lê direto.
**`LOG.add(text, color)` manteve a MESMA assinatura** — os ~145
call-sites espalhados por `skill_handlers.py`/`spell_system.py`/
`world_systems.py`/etc. (dano, cura, proc, loot) não mudaram NADA, só
chamam `.add()` e nunca liam estado interno. Decisão do usuário: remover
o popup flutuante de vez (sem redundância com a aba nova) — não os dois
juntos.

**Scroll ancorado no FUNDO (não no topo, diferente de outras listas do
projeto):** `_chat_scroll[aba] = 0` sempre mostra as mensagens mais
recentes (segue o chat automaticamente); rolar a roda pra cima
AUMENTA o scroll (revela histórico mais antigo) — convenção oposta à de
listas ancoradas no topo tipo `debug_handlers.py::_debug_item_scroll`
(lá, `scroll=0` é o INÍCIO da lista, rolar pra cima diminui). Cada aba
lembra seu próprio scroll ao trocar de aba.

Aba Combate nunca abre campo de digitação (`_open_chat_input` vira no-op
se `self._chat_tab == "combat"`) — é só leitura.

**Persistência em arquivo:** avaliada e descartada por enquanto (não é
requisito) — cap de 500/aba já resolve memória; fica registrado como
ideia de v2 se um dia fizer falta (suporte a jogador, revisar log após
fechar o jogo).

Validado (headless): `combat_log.py` cap de 500 + ordem + assinatura de
`add()` preservada; lógica de `chat_handlers.py` (clique fora da janela
não consome, clique em aba troca `_chat_tab`, aba Combate bloqueia input,
geração de linhas com wrap, scroll segue mensagem mais recente em
scroll=0, direção do scroll, aba Combate lê `LOG.entries`). Suíte
completa sem regressão (9F/83P — zero mudança server-side).

**Não validado:** passada manual com o jogo rodando (visual das 3 abas,
scrollbar arrastando de verdade, canal Mundial com 2 clientes reais).

### 19. Reorganização da raiz — content/engine/ui/debug/tools/release_tools (09/07/2026)

A raiz tinha ~60 arquivos `.py` soltos sem nenhuma pasta (dados de
conteúdo, sistemas ECS headless, UI/render client-only e scripts de dev
todos misturados). Reorganizados em 6 pastas novas, mantendo `server/`/
`client/`/`shared/` (a separação MAIS importante do projeto) intactos:

- **`content/`** — tabelas de dado puro (`mob_definitions`, `item_table`,
  `loot_tables`, `merchant_data`, `quests_data`, `talent_data`,
  `skill_config`, `enemy_abilities_data`, `crafting_data`,
  `status_effects_data`).
- **`engine/`** — ECS headless compartilhado client+server (`world`,
  `components`, `entity_factory`, `core_systems`, `world_systems`,
  `stat_fns`, `stats_system`, `damage_calculator`, `quest_logic`,
  `quest_events`, `save_system`, `utils`, `fx`, `map_loader`, `tileset`).
- **`ui/`** — client-only (render/HUD/painéis/áudio) — **critério não foi
  "importa pygame?" e sim "quem carrega isso de verdade"**: `combat_log.py`,
  `skill_handlers.py` e `fov.py` são headless-clean mas só `ui/systems.py`/
  `game.py` os importam (servidor nunca) — foram pro `ui/` mesmo assim.
  Verificado via grep no grafo de imports real (`grep -rl "import X" server/`),
  não por inspeção do topo do arquivo.
- **`debug/`** — `aoi_debug.py`/`mob_combat_debug.py`: módulos de log
  ATIVOS importados por server E client em runtime (gated por flag) — NÃO
  são scripts soltos, por isso pasta própria em vez de ir pro `tools/`.
- **`tools/`** — `check_surfaces.py`/`png_to_map.py`/`reverb.py`: scripts
  standalone com **zero importador** em todo o repo (confirmado via grep) —
  rodados manualmente pelo dev, nunca pelo jogo.
- **`release_tools/`** — `build_client.ps1` + `rpg_online_client.spec`.
  **NUNCA nomear essa pasta `packaging/`** — colide com a lib real do PyPI
  `packaging` (usada pelo próprio PyInstaller internamente via
  `import packaging.requirements`); como o CWD entra no `sys.path` como
  namespace package implícito (PEP 420, nem precisa de `__init__.py`), uma
  pasta local `packaging/` na raiz é encontrada ANTES da lib de verdade e
  quebra qualquer ferramenta que dependa dela — foi exatamente o que
  aconteceu no primeiro build de teste desta migração (`ModuleNotFoundError:
  No module named 'packaging.requirements'`), só descoberto rodando o build
  de verdade, não só a suíte de testes (pytest não quebrou com a colisão —
  não confiar só nos testes pra validar mudança de nome de pasta na raiz).

**Mecânica da migração** (não manual — 90 arquivos e centenas de imports):
`git mv` de cada arquivo, depois script Python com regex por forma de
import (`import X` → `import NOVO.CAMINHO as X` — alias preserva TODO
call-site `X.attr` existente sem precisar tocar nele; `from X import Y` →
`from NOVO.CAMINHO import Y`, nomes importados não mudam). Nomes
processados do mais longo pro mais curto (`world_systems` antes de
`world`) — evita colisão de substring (`\bworld\b` não bate dentro de
"world_systems" porque `_` é caractere de palavra em regex, mas a ordem
ainda importa pro `from X import`/`import X` ficarem exatos). Casos
especiais tratados à parte: `__import__("components").Attr")` (usado ~20x
pra import tardio, evitar ciclo) virou
`__import__("engine.components", fromlist=["Attr"]).Attr` — `__import__`
com path pontuado sem `fromlist` retorna o pacote TOP-LEVEL, não o
submódulo, então trocar só a string sem adicionar `fromlist` quebraria
silenciosamente (o `.Attr` seguinte falharia com `AttributeError`).

`paths.py`/`config.py` ficaram DE PROPÓSITO na raiz (não entraram em
`engine/`) — ambos resolvem local via
`os.path.dirname(os.path.abspath(__file__))` assumindo estar ao lado de
`assets/`/`maps/`/`config.json`; mover pra uma subpasta mudaria o
`__file__` e quebraria a resolução de asset em modo dev (não-frozen).

`release_tools/rpg_online_client.spec` precisou de um fix estrutural:
`Analysis(["main.py"])` é resolvido pelo PyInstaller relativo ao
**SPECPATH** (pasta do `.spec`), não ao CWD de onde foi invocado — passar a
viver em `release_tools/` quebrava `main.py` (`ERROR: script ... not found`)
até trocar pra `os.path.join(SPECPATH, "..", "main.py")`. `build_client.ps1`
também mudou seu `Set-Location` de `$PSScriptRoot` pra
`$PSScriptRoot\..` (volta pra raiz do projeto, de onde `assets/`/`maps/`/
`dist/` sempre foram resolvidos).

Validado: `py_compile` em 100% dos `.py` do repo; suíte completa sem
regressão (9F/83P — mesma baseline de sempre); rebuild completo via
PyInstaller (`release_tools/build_client.ps1`) — mesmo tamanho final
(52,5 MB); exe gerado testado (roda sem `crash.log` nos primeiros
segundos, mesma checagem usada nos builds anteriores).

**Não validado:** sessão de jogo manual completa com o build novo (só
smoke-test de processo vivo/sem crash imediato).

### 20. Modo Evasão (estilo WoW) para mobs em RETURNING (09/07/2026)

**Reportado pelo usuário:** quando um mob estoura o leash (raio de
perseguição, ver `EnemyAISystem`/`MAX_LEASH_RADIUS`) e entra em
`RETURNING` (voltando pro spawn), ele NÃO era imune a dano nem a
re-aggro — bater nele durante o retorno resetava a perseguição de graça.
Como mobs nunca regeneram HP (não existe regen de mob no código), o dano
acumulado nesses ciclos ficava permanente até o mob morrer — "prato cheio
pra bug abusers" (palavras do usuário).

**Decisão de design:** reaproveitar `AIControlled.state == "RETURNING"`
como o próprio marcador de evasão, sem criar campo novo — já é a única
exclusão do `in_attack_range`, já é sincronizado em rede, evita 2 fontes
de verdade. Ao chegar no spawn (3 pontos de saída de `RETURNING` no
`EnemyAISystem.update()`), reset completo: cura HP pra `max_hp` + limpa
`StatusEffects` (debuffs/DoTs residuais) — não só a cura, confirmado com
o usuário (estilo WoW: mob volta 100% novo pro combate seguinte).

**Implementação — 4 "portas de entrada" de dano, nenhuma passa por uma
função comum antes de escrever HP:**
- `apply_damage_core()` (`engine/core_systems.py`) — novo guard
  `"blocked_evade"` (mesmo padrão de `"blocked_immune"`), rede de
  segurança final que cobre qualquer chamador presente ou futuro. Sozinho
  não dá feedback visual correto — por isso os 4 pontos abaixo também têm
  guard cedo, ANTES de rolar acerto/crit/conceder skill xp (sem isso, um
  golpe bloqueado ainda concedia xp de arma/resistência ao alvo evadindo).
- `CombatSystem.deal_damage()` (`engine/world_systems.py`, melee/geral) —
  guard cedo + `_emit_avoidance_feedback("evade", ...)` (diferente de
  `is_immune`, que é silencioso — aqui o feedback é visível de propósito,
  pedido explícito do usuário). Novo outcome `'evade'` em `_AVOID`/
  `_AVOID_LOG`/`_SND` (indexação direta, sem `.get()` — as 3 precisavam da
  entrada nova ou dava `KeyError`).
- `_server_apply_magic_damage()`/`_server_apply_ranged_physical()`
  (`server/spell_completion_processor.py`) — guard cedo via novo helper
  `_is_evading(target_id)`; `_apply_ranged_physical` retorna `"evade"`
  igual já fazia com `"immune"`.
- `_apply_magic_damage()` (`ui/spell_system.py`, client-offline) — mesmo
  guard, espelhado.
- `StatusEffectSystem._apply_tick()` (`engine/core_systems.py`, tick de
  DoT/HoT) — escreve HP direto, não passa por `apply_damage_core`; guard
  próprio ao lado do `is_immune` já existente ali.

**Extensão de escopo confirmada com o usuário** — fechar o vazamento de
efeitos SECUNDÁRIOS (mesmo buraco pré-existente que já afetava Bloco de
Gelo/`is_immune`, não introduzido por esta mudança): dos ~7 handlers de
skill mágica/à distância que chamam as duas funções acima
(`_server_bola_de_fogo`, `_server_calcinar`, `_server_nova_congelante`,
`_server_picada_escorpiao`, `_server_flecha_reiterada`,
`_server_tiro_repulsivo`, `_server_tiro_multiplo_hit`), 3 precisaram de
guard explícito via `_is_evading()` (bola de fogo, calcinar — burn/
exaustão vazavam mesmo com dano bloqueado; nova congelante, dentro do
loop AOE, já que é multi-alvo). `tiro_repulsivo` tinha um bug: seu guard
de outcome (`if outcome in ("miss","dodge","parry","immune"): return`)
não incluía `"evade"` — sem o fix, o KNOCKBACK ainda empurraria/stunaria
um mob evadindo mesmo com o dano direto já bloqueado. Os outros 3
(picada_escorpiao, flecha_reiterada, tiro_multiplo_hit) já eram seguros
por construção (checam `dmg > 0` antes do efeito secundário, ou não têm
efeito secundário nenhum).

**Re-aggro:** os 4 pontos que já existiam de "dano de player re-agra mob
parado" (todos com o mesmo gate `_ai.state in ("IDLE", "RETURNING")`,
inconsistentes entre si: `deal_damage`/`_server_apply_magic_damage` usam
`AGGRO_DELAY` 0.5s, `_server_apply_ranged_physical` pula direto pra
`CHASING` — inconsistência pré-existente, não mexida aqui) tiveram o gate
apertado pra `_ai.state == "IDLE"` — depois do guard de evasão acima, esse
código já é inalcançável pra um mob RETURNING, mas o aperto remove a
intenção enganosa do código (uma refatoração futura que reordenasse os
blocos poderia reintroduzir o bug silenciosamente).

**Bug pego durante a própria validação:** os 2 pontos de reset (A e B)
inicialmente curavam/limpavam debuff em TODO tick de qualquer mob IDLE já
parado perto de casa sem alvo (o "no-op de manutenção" mais comum do
jogo), não só na transição de saída de `RETURNING` — pego pela suíte
(6 testes de dano/morte de mob começaram a falhar: HP nunca baixava,
porque o mob se auto-curava antes do próximo tick rodar). Corrigido
capturando `_was_returning` antes de sobrescrever `ai_control.state`, e
só curando/limpando debuff se o estado anterior era de fato `RETURNING`.
Também exigiu ajustar `tests/helpers.py::teleport_mob_to_player` — o
helper movia o mob sem realinhar `InitialPosition`, deixando-o
"impossivelmente longe" do próprio spawn e disparando `RETURNING` em
qualquer teste de combate que teleporta o mob perto do player (agora
realinha `InitialPosition` junto).

Validado: `py_compile` em 100% dos arquivos tocados; suíte completa —
**7F/85P** (melhora sobre a baseline 9F/83P: 2 falhas pré-existentes
sumiram, nenhuma nova, mesmas 7 falhas de sempre); script headless
dedicado cobrindo os 4 caminhos de dano (`apply_damage_core` direto,
`CombatSystem.deal_damage` melee, `_server_apply_ranged_physical` +
`_server_tiro_repulsivo` sem knockback, `_server_apply_magic_damage` +
`_server_bola_de_fogo` sem burn secundário) + DoT parando de tickar +
reset completo (cura full, limpa debuff, volta a `IDLE`) ao chegar no
spawn — todos os asserts passaram.

**Não validado:** sessão de jogo manual com o cliente pygame de verdade
(puxar mob até o leash visualmente, confirmar o feedback "Evadiu!" na
tela, e os 3 caminhos de dano em multiplayer real com 2+ clientes).

### 20.1 Revisão: cura instantânea → regen gradual (1%/3s) + fix de sync (09/07/2026)

**Reportado pelo usuário** após validar a Decisão 20 no jogo de verdade: o
HP do mob no client não atualizava quando ele curava ao sair da evasão.
Causa raiz: a cura instantânea (`enemy_combat_stats.current_hp =
enemy_combat_stats.max_hp`, dentro de `EnemyAISystem.update()`, código
COMPARTILHADO client+server) nunca passava por nenhum canal de
broadcast — mudar `current_hp` direto no componente ECS servidor não
propaga sozinho pro client; só client sabe que o HP mudou quando algo
enfileira um evento em `_combat_this_tick`/`_pending_mob_attacks` (que
viram o array `"combat"` de `AOI_UPDATE`, consumido por
`client/remote_entity_handlers.py`, linha ~199-205, que atualiza
`RemoteEntityMeta.hp` genericamente pra qualquer entrada com
`hp_after` — não precisa ser um "hit" de verdade).

**Decisão (usuário):** trocar a cura instantânea por regen gradual —
**1% do max_hp a cada 3 segundos**, enquanto o mob está fora de combate
(`state` em `IDLE`/`RETURNING`), até `max_hp`. Mesmo mecanismo/canal já
usado pelo HP5 de player (`BaseCombatStateSystem._tick_hp5`,
`engine/core_systems.py`) e pelo regen do Boneco de Treino
(`WorldServer._tick()`, ~linha 2469) — ambos já emitem
`{"attacker": -1, "outcome": "regen", "hp_after": ...}` em
`_combat_this_tick`, que É broadcast a QUALQUER observador com o mob em
`known_eids` (não só um "dono", já que mob não tem dono — ver
`server/session.py::_build_update_for_session`).

**Implementação:**
- `engine/components.py`: novo campo `AIControlled.regen_timer: float`
  (acumulador dedicado — NÃO reaproveita `CombatStats.hp5_timer`, que já
  assume 5s pra player/dummy; o intervalo do mob é 3s, diferente).
  `CombatStats.hp5` (fração por tick, default `0.01` = 1%) já existe e
  **nunca é sobrescrito por mob comum** em `entity_factory.create_enemy`
  — só o TrainingDummy customiza — então reaproveitar esse campo pro
  VALOR (1%) já dava de graça, só faltava o tick com intervalo próprio.
- `server/world_server.py::_tick()`: novo bloco "Regen de mob fora de
  combate", ao lado do regen do Boneco de Treino — itera `self._mob_eids`,
  pula mob morto/já em combate (`state not in ("IDLE","RETURNING")`)/já
  em max_hp, acumula `dt` em `regen_timer`, a cada 3s cura
  `max(1, round(max_hp * hp5))` e enfileira o evento `"regen"` em
  `_combat_this_tick` (mesmo formato do HP5/dummy).
- `engine/world_systems.py::EnemyAISystem.update()`: os 2 pontos de
  chegada no spawn (RETURNING→IDLE) **não curam mais HP instantaneamente**
  — só limpam debuffs/DoTs residuais (isso continua instantâneo, não fazia
  parte da reclamação). HP agora só sobe via o novo regen gradual.

**Reentrada no AOI (a outra parte do pedido do usuário) — já funcionava
corretamente, confirmado por investigação, sem necessidade de fix:**
`session.known_eids` é um `set` por sessão, descartado (`.discard(eid)`)
sempre que um mob sai do AOI de um player, e todo re-ingresso (seja por
movimento do mob, seja por sweep de posição estática) sempre busca um
payload de spawn FRESCO via `get_entity_spawn_data()` →
`_build_mob_spawn_payload()`, que lê `cs.current_hp` ao vivo no momento
da chamada — nunca um valor cacheado/antigo. Um mob que sai da tela e
volta sempre chega com o HP atual do servidor.

Validado: `py_compile` completo; suíte sem regressão (7F/85P, mesma
baseline pós-Decisão-20); script headless dedicado confirmando (a) mob
em combate NÃO regenera, (b) mob fora de combate regenera exatamente
`round(max_hp×0.01)` a cada 3s E emite o evento de broadcast
`{"outcome":"regen","hp_after":...}`, (c) não regenera acima de `max_hp`.

**Não validado:** client de verdade mostrando a barra de HP do mob subir
gradualmente (o client já consome esse canal genericamente pra outros
casos de regen — player HP5, dummy — mas nunca foi visto rodando pra
mob; também não há feedback visual tipo "+HP" pra mob regenerando, só
pra player — cosmético, não pedido pelo usuário).

### 20.2 HP5/MP5 viram atributos por classe + atributo Spirit (09/07/2026)

**Contexto:** o regen de mob (20.1) usou o mesmo mecanismo do HP5 de
player, que até aqui era um valor GLOBAL fixo (`CombatStats.hp5 = 0.01`,
1%/5s pra todo mundo) — o usuário percebeu essa discrepância na conversa
e pediu pra tornar HP5/MP5 atributos de verdade do personagem, com um
atributo novo **Spirit** (`CharacterStats.spirit`) alimentando os dois:
10 pontos de Spirit = +1% de HP5/MP5.

**Decisão final do usuário (após 2 rodadas de perguntas):**
1. Spirit só afeta a regen de mana FORA de combate (`mp5`). Mana EM
   combate (`mp5_ic`) é controlada exclusivamente por talento — Spirit
   nunca entra ali. HP segue a mesma regra de sempre (só regenera fora
   de combate).
2. Spirit NÃO cresce com level (`CLASS_LEVEL_GAINS` não tem entrada pra
   ele, de propósito) — como o regen é percentual, deixar Spirit crescer
   por level somaria com item/talento até virar um regen absurdo. Em vez
   disso, cada classe tem uma **base FIXA** de hp5/mp5/mp5_ic (não deriva
   de nenhum atributo), e Spirit (só de item/talento futuro — nada seta
   ele hoje) soma POR CIMA dessa base:
   - Arqueiro: 1% HP5 (sem mana)
   - Guerreiro: 3% HP5 (sem mana)
   - Mago: 1% HP5, 4% MP5 fora de combate, 1% MP5 em combate

**Implementação — mesmo padrão "fonte única" já usado pros outros 5
atributos (STR/INT/AGI/VIT/DEF), ver `CLAUDE.md`:**
- `engine/components.py`: `CharacterStats.spirit: int = 0` (default 0,
  SEM entrada em `CLASS_BASE_STATS` — todas as classes começam em 0,
  só item/talento futuro incrementa). `PermanentStats.spirit: int = 0`
  (mesma mecânica roguelike dos outros 5 — soma na morte). `CombatStats`
  ganha os pares `base_hp5`/`hp5` (já existia, sem base_ antes),
  `base_mp5`/`mp5` (novo — mana fora de combate) e
  `base_mp5_ic`/`mp5_ic` (novo — mana em combate, nunca leva Spirit).
- `engine/stat_fns.py`: `"hp5"`, `"mp5"`, `"mp5_ic"` entram em
  `_MODIFIABLE_ATTRS` (+ clamp `(0.0, None)`) — item/talento futuro que
  queira dar bônus de regen já funciona de graça via `Modifier`, mesmo
  mecanismo de `attack_power`/`crit_rating`/etc., sem precisar de código
  novo.
- `engine/stats_system.py`: nova tabela `CLASS_BASE_REGEN` (valores
  fixos acima) + `apply_char_stats_to_combat()` agora computa
  `total_spirit` (personagem + `PermanentStats`, mesmo padrão dos outros
  atributos) e seta `base_hp5 = CLASS_BASE_REGEN[classe]["hp5"] +
  spirit×0.001`, `base_mp5` igual, e `base_mp5_ic = CLASS_BASE_REGEN[...]
  ["mp5_ic"]` **sem** somar spirit.
- `engine/core_systems.py::_tick_mana_regen`: assinatura ganhou o
  parâmetro `cst` (CombatStats) — a taxa não vem mais das constantes
  globais `MANA_REGEN_OOC_PCT`/`MANA_REGEN_IC_PCT` (removidas), vem de
  `cst.mp5`/`cst.mp5_ic`. 2 call sites atualizados
  (`ServerCombatStateSystem.update()` e `ui/spell_system.py::ManaSystem`,
  client-offline).
- Mob **não é afetado** — nunca passa por `apply_char_stats_to_combat`
  (não tem `CharacterStats`), então mantém `hp5=0.01` (o default de
  `CombatStats.__init__`) exatamente como no regen gradual de 20.1.
- Save/load (`engine/save_system.py`) e sync de rede
  (`server/world_server.py` ×3 pontos, `client/save_sync_handlers.py`,
  `client/network_handlers.py`) passaram a incluir `spirit` — saves
  antigos sem o campo caem no default 0 automaticamente (sem migração
  necessária, já que 0 é exatamente o valor "correto" pra um personagem
  que nunca ganhou Spirit de item/talento).

**Fora de escopo (explicitamente, por instrução do usuário):** nenhum
item/talento existente foi alterado pra conceder Spirit — o atributo
existe e o cálculo funciona, só não há NENHUMA fonte que o incremente
ainda (fica pronto pra quando isso for implementado).

Validado: `py_compile` completo; suíte sem regressão (7F/85P). Script
headless dedicado confirma os valores exatos por classe (guerreiro 3%
hp5/0% mp5, mago 1%/4%/1%, arqueiro 1%/0%), que Spirit (personagem E
`PermanentStats`) soma corretamente em hp5/mp5 mas NUNCA em mp5_ic, e que
mob continua com hp5=0.01 (não afetado). `_tick_mana_regen` testado
isoladamente confirma a taxa correta aplicada dentro/fora de combate
(um teste inicial via tick de mundo completo deu resultado errado por
um mob próximo manter o player em combate de verdade via aggro —
artefato do ambiente de teste, não bug de produção; resolvido isolando a
função pura sem o resto do mundo).

**Não validado:** sessão manual — criar um Guerreiro/Mago/Arqueiro e
observar a régua de vida/mana regenerando na taxa certa em tempo real.

### 21. Auto-attack do arqueiro nunca validava linha de visão (LOS) — 09/07/2026

**Reportado pelo usuário (bug recorrente, já reportado outras vezes):**
atirar num alvo com obstáculo na frente tocava o som de disparo, descontava
flecha da aljava e agrava o mob — mas sem dano nenhum e sem o projétil
aparecer. Variante relatada também: às vezes o ataque fica silencioso mas
ainda desconta flecha.

**Causa raiz (confirmada por investigação exaustiva):** em todo o pipeline
de auto-attack (client trigger → som → servidor → consumo de flecha →
agro → dano), o **único** ponto que checava linha de visão era um check
puramente **cosmético e tardio demais**, do lado do CLIENTE, dentro de
`ui/spell_system.py::PlayerProjectileSystem.update()` — ele destruía o
projétil visual (`PlayerProjectile`) silenciosamente ao detectar parede,
MAS isso rodava só DEPOIS do servidor já ter aplicado dano de verdade,
descontado a flecha e agrado o mob (`server/spell_completion_processor.py::
_server_apply_ranged_physical` e `server/combat_processor.py::
_process_player_attacks` nunca checavam obstáculo — só distância
Chebyshev). Ou seja: o servidor sempre deixava o tiro "acontecer" de
verdade através da parede; o cliente só escondia visualmente o resultado
depois do fato consumado. Esse mesmo destroy silencioso também nunca
limpava `pending_arrow_impacts` (fila de outcomes pendentes por alvo),
deixando uma entrada órfã que uma flecha SEGUINTE no mesmo alvo aplicava
por engano (outcome/dano errado/velho) — bug secundário do mesmo código.

**Fix — 3 pontos:**
1. **`server/spell_completion_processor.py::_server_apply_ranged_physical`**
   — novo guard de LOS logo no topo da função (mesmo padrão dos guards de
   `_is_evading`/alvo morto já existentes), usando
   `EnemyAISystem._has_line_of_sight` (mesma primitiva Bresenham que
   `EnemyAISystem` já usa pro lado mob→player, nunca antes usada pro lado
   player→mob) + lookup de tilemap multi-mapa (`get_entity_map`/
   `_map_bundles`, mesmo padrão de `_server_tiro_repulsivo`). Retorna
   `(False, "miss", 0)` — MESMO outcome de um erro de verdade, então todo
   o resto do pipeline (client, `_combat_this_tick`, feedback) já sabe
   lidar sem nenhuma mudança adicional: sem LOS, não há consumo de flecha
   nem agro (ambos só rodam depois do guard, na mesma função). Cobre
   auto-attack E as skills que reusam esta função (Picada de Escorpião,
   Flecha Reiterada, Tiro Repulsivo, Tiro Múltiplo).
2. **`ui/systems.py::_process_archer_combat`** — mesmo guard de LOS
   ANTES do bloco de predição otimista local (`if self._net: quiver.
   arrow_count -= 1; ...`), usando a mesma primitiva (já reexportada por
   `ui/systems.py`). Evita a aljava exibida no HUD cair achando que o
   tiro vai sair quando o servidor já vai bloquear.
3. **`ui/spell_system.py::PlayerProjectileSystem.update()`** — o check de
   LOS por-frame (cosmético) agora **exclui** `spell_id=="arrow"` (flecha
   de auto-attack): como o servidor já valida LOS na origem, um tiro
   bloqueado agora chega como `outcome="miss"` pelo canal normal de
   `COMBAT_RESULT`, e cai no bloco de resolução de outcome já existente
   (flecha desvia visualmente, "Errou!", consome `pending_arrow_impacts`
   corretamente) em vez de ser destruído cedo demais e silenciosamente.
   Mantido para projéteis de skill de verdade (Bola de Fogo etc.) — ali
   o alvo pode se esconder atrás de parede DURANTE o voo, cenário que o
   outcome já resolvido no lançamento não cobre.

Validado: `py_compile` completo; suíte sem regressão (7F/85P). Script
headless dedicado: tiro com parede no meio do caminho retorna
`outcome="miss"`, HP/flecha/estado de IA do mob inalterados; removendo a
parede, o MESMO tiro conecta normalmente (hit + consumo de flecha) —
confirma que o fix bloqueia só quando deveria, sem quebrar tiros válidos.

**Não validado:** sessão manual (visual do redirecionamento da flecha
bloqueada, som, e o caso "silencioso mas consome" — esse último tem causa
DIFERENTE, ainda não corrigida: `_apply_combat_result` só toca som/spawna
projétil se o mob já estiver rastreado localmente pelo AOI do cliente
— `client/remote_entity_handlers.py`, `local_eid = self._remote_mobs.get(...)`;
se o COMBAT_RESULT chega numa janela em que o cliente ainda não conhece o
mob, o servidor (corretamente autoritativo) já aplicou o resultado mas o
cliente não tem o que renderizar — janela de corrida ligada a timing de
AOI, não coberta por este fix, mais rara que o caso de parede).

### 21.1 Revisão: LOS bloqueada deve impedir o ataque INTEIRO, não virar "miss" (09/07/2026)

**Reportado pelo usuário, testando a Decisão 21:** com obstáculo no
caminho, o comportamento ficou quase o inverso do bug original — o
projétil agora era criado, acertava a posição do mob, mostrava "Erro" no
floating text e tocava o som de disparo, só que sem descontar flecha nem
agrar. Ou seja: o fix anterior tratou obstáculo como um **"miss" de
verdade** (dano 0, sem consumo, sem agro, mas AINDA com som+projétil
visíveis) — o usuário corrigiu: o certo é **nada acontecer**, igual estar
fora de alcance — sem som, sem projétil, sem qualquer efeito colateral.

**Fix:** moveu a checagem de LOS pra **antes** de qualquer coisa
acontecer, direto em `server/combat_processor.py::_process_player_attacks`
— logo depois do check de range (`_srv_dist > attack_range: continue`) e
ANTES do cooldown, mesmo tratamento: obstáculo faz o ataque nem ser
tentado neste tick (nenhum `_combat_this_tick.append(...)`, logo nenhum
`COMBAT_RESULT` é emitido — sem isso o client nunca chama
`_apply_combat_result`, então nunca toca som nem spawna projétil).
Cooldown não é consumido (igual ao check de range) — assim que a LOS
desobstrui, o tiro sai imediatamente, sem esperar o intervalo de ataque
"desperdiçado" num tiro que nunca aconteceu.

O guard de LOS dentro de `_server_apply_ranged_physical` (Decisão 21,
retorna `"miss"`) **continua existindo** — vira rede de segurança só pras
SKILLS que reusam essa função (Tiro Repulsivo, Picada de Escorpião, Flecha
Reiterada, Tiro Múltiplo): essas já commitam visualmente o lançamento
(cast bar + broadcast de "launch") ANTES do hit resolver, então não dá
pra simplesmente "não acontecer" — a skill já foi visualmente disparada,
então tratar como miss (flecha desvia, sem dano/consumo/agro) é o
comportamento certo pra elas. Só o auto-attack — que nunca commitou nada
visualmente antes deste ponto — ganhou o bloqueio total.

Validado: `py_compile` completo; suíte sem regressão (7F/85P). Script
headless reescrito pra rodar o pipeline completo (`_process_player_attacks`
via ticks reais, não a função de dano isolada): com parede, **zero**
entradas em `_combat_this_tick` pro mob (nenhum `COMBAT_RESULT`), HP/
flecha/estado de IA inalterados; sem parede, ataques disparam normalmente
no intervalo esperado, com consumo de flecha correto. Teste anterior
(chamada direta a `_server_apply_ranged_physical`, retornando `"miss"`)
continua passando — confirma que o guard interno (usado pelas skills)
não foi quebrado pela mudança.

**Não validado:** sessão manual confirmando que nenhum som/projétil
aparece mais num tiro bloqueado, e que skills com obstáculo ainda mostram
o redirecionamento visual de "miss" corretamente.

### 21.2 Causa raiz achada via log: LOS bloqueada esgotava is_pursuing (regressão da 21.1) — 09/07/2026

**Reportado pelo usuário:** depois da Decisão 21.1, o arqueiro "do nada"
parava de atacar, e voltava o bug de descontar flecha da aljava sem gerar
flecha/dano. Instrumentado `debug/archer_debug.py` (client+server,
eventos ATTEMPT/BLOCK/LOS/FIRE/ARROW/AGGRO/SOUND/RECV) e pedido pro
usuário reproduzir — o log confirmou a causa raiz com precisão de
milissegundo.

**Causa raiz (confirmada pelo log, não suposição):** o log do servidor
mostrou o player em combate contra um mob, alternando `los_blocked`/
`cooldown` (alvo entrando/saindo de cobertura), e então:
```
[21:12:50.902] FIRE      outcome=miss
...(só los_blocked/cooldown, nenhum FIRE)...
[21:12:56.937] BLOCK     reason=not_pursuing
```
**Exatos 6.035s entre o último ataque de verdade e `is_pursuing` virar
`False`** — bate com `CombatState.OUT_OF_COMBAT_DURATION = 6.0`
(`engine/components.py`). A Decisão 21.1 moveu o bloqueio de LOS pra
**antes** do `continue` de cooldown — mas o `enter_combat()` que reseta
`combat_timer` só roda **depois** do cooldown, na seção "Ataque disparou"
(`server/combat_processor.py`, ~linha 217). Resultado: um tiro bloqueado
por LOS nunca mais chamava `enter_combat()`. Se o alvo passa 6s+
alternando dentro/fora de cobertura sem NENHUM tiro desbloqueado passar
(nem hit nem miss — os dois chamavam `enter_combat` antes),
`_tick_combat_timer` (`engine/core_systems.py`) deixa `combat_timer`
chegar a zero e força `in_combat=False` + **`is_pursuing=False`** — o
arqueiro para de atacar de verdade, servidor autoritativo.

**A segunda metade do bug (desconta sem gerar flecha) é consequência
direta:** `is_pursuing` do SERVIDOR nunca é resincronizado de volta pro
CLIENTE — é um valor que o cliente seta uma vez (clique direito) e nunca
mais reavalia sozinho. Uma vez que o servidor desiste silenciosamente, o
`combat_state.is_pursuing` local do cliente continua `True` pra sempre, e
`ui/systems.py::_process_archer_combat` continua rodando o bloco de
predição otimista (`quiver.arrow_count -= 1`) a cada cooldown local — sem
NUNCA receber um `COMBAT_RESULT` de volta (`RECV` sumiu do log do
cliente na mesma janela), já que o servidor não dispara mais nada.
Exatamente o "desconta flecha sem gerar flecha".

**Fix:** `enter_combat(player_cst)` agora roda também no `continue` de
LOS bloqueada (`server/combat_processor.py`) — o disparo em si continua
100% bloqueado (sem som, projétil, flecha, agro — a decisão 21.1
continua valendo), mas **tentar atirar conta como estar em combate**,
então `combat_timer` nunca expira só por causa de obstáculo, e
`is_pursuing` nunca cai sozinho enquanto o player está genuinamente
tentando lutar.

**Risco residual (não corrigido, fora de escopo desta rodada):** o gap
arquitetural raiz — `is_pursuing` do servidor nunca é resincronizado pro
cliente — continua existindo pra QUALQUER outro motivo de timeout (não
só LOS). Esta correção fecha o caso concreto reproduzido (o único
conhecido até agora), mas se outro caminho ainda inexplorado também
zerar `is_pursuing` no servidor sem o cliente saber, o mesmo sintoma
("desconta sem gerar flecha") pode reaparecer por uma causa diferente.
Uma correção mais robusta seria sincronizar `is_pursuing` explicitamente
(ex.: via `STATS_UPDATE` ou canal equivalente) sempre que o servidor
mudar esse valor — não implementado agora por ser uma mudança maior de
protocolo, mas registrado aqui como próximo passo se o sintoma voltar por
outro caminho.

Validado: `py_compile` completo; suíte sem regressão (7F/85P); script
headless confirmando que `is_pursuing`/`in_combat` sobrevivem a LOS
bloqueada sustentada (o teste tem uma variável não totalmente controlada
— o mob pode se mover durante o teste — mas mostra qualitativamente
`combat_timer` erodindo sem o fix e se mantendo no máximo com o fix,
consistente com a teoria; a evidência forte de verdade é o log real do
usuário, com o match exato de 6.0s).

**Não validado:** sessão manual reproduzindo o cenário exato do log
(mob alternando dentro/fora de cobertura por mais de 6s) e confirmando
que o arqueiro não para mais de atacar.

---

### 21.3 Causa raiz REAL de "desconta flecha sem projétil": cooldown local não congela igual ao do servidor — 10/07/2026

**Reportado pelo usuário (após 21.2 já em produção):** "ainda desconta
flechas da aljava algumas vezes sem aparecer o projétil sair, e também
uma das vezes parou de atacar, tive que clicar novamente com o direito
no alvo para voltar a atacar." A Decisão 21.2 corrigiu o caso concreto
que ela mesma diagnosticou (timeout de `is_pursuing` por LOS bloqueada
6s+), mas o sintoma central (desconto sem flecha) continuou.

**Causa raiz (medida diretamente no log, não suposição):** comparando o
mesmo teste de ~124s (sessão 21:37:22) nos dois lados:
- Servidor: 29 disparos resolvidos de verdade (`FIRE`, hit+miss juntos),
  dos quais só 15 consumiram flecha real (`ARROW`, só em hit/crit — miss
  não consome, `spell_completion_processor.py` linha ~1136-1140 só chega
  lá depois de `_apply_final_damage` ter sucesso).
- Cliente: **52 descontos locais** de `quiver.arrow_count`
  (`ui/systems.py::_process_archer_combat`, nota
  `predicao_local_otimista`) no mesmo intervalo — 23 a mais que o total
  de disparos reais do servidor, e nenhum correspondente a um tiro que
  nunca existiu (contagem via `RECV`, que bateu exatamente com os 29
  disparos reais — nenhuma mensagem perdida na rede).

**Por quê:** o congelamento de cooldown durante bloqueio (LOS/alcance/
perseguição/aljava vazia) funciona de formas ARQUITETURALMENTE
diferentes nos dois lados:
- **Servidor** (`server/combat_processor.py::_process_player_attacks`):
  os checks de `not_pursuing`/`no_quiver_or_empty`/`out_of_range`/
  `los_blocked` rodam **todo tick, incondicionalmente**, e todos usam
  `continue` **antes** de `self._attack_timers[session_id] -= dt` (linha
  ~202). Ou seja, o cooldown real fica congelado durante O BLOQUEIO
  INTEIRO, tick a tick, não importa o valor atual do timer.
- **Cliente** (`ui/systems.py::update`, linha ~479-480): o decremento de
  `combat_stats.attack_cooldown_timer` é genérico e **incondicional**,
  rodando todo frame pra QUALQUER classe, **antes** de
  `_process_archer_combat` sequer saber se o tiro seria bloqueado. Os
  checks de LOS/alcance/aljava só são avaliados **depois** que o timer já
  chegou a zero (dentro do `if attack_cooldown_timer <= 0:`) — então o
  cooldown local termina de contar (de cheio até zero) mesmo durante todo
  um bloqueio (perseguindo fora de alcance, ou LOS piscando dentro/fora
  de cobertura repetidamente, como no log). Quando o bloqueio abre uma
  brecha mesmo que breve, o timer já está zerado há tempo e dispara
  IMEDIATAMENTE (desconta flecha), enquanto o servidor — que só conta o
  cooldown durante os ticks em que NÃO houve bloqueio — ainda não estava
  pronto de verdade. Resultado: desconto local sem tiro real por trás.

**Fix (mais simples e robusto que tentar espelhar o congelamento exato
do servidor):** o desconto de `quiver.arrow_count` na predição otimista
não tinha motivo pra existir de forma antecipada — o comentário do
próprio código já dizia "flecha 100% server-driven" (a flecha VISUAL só
nasce ao chegar `COMBAT_RESULT`, nunca por timer local). Só o CONTADOR
da aljava é que ainda descontava cedo. Solução: parar de descontar
`quiver.arrow_count` em `_process_archer_combat` (mantém só cooldown/
pré-tensionamento locais, que são só UI e não controlam a cadência real
de disparo do servidor — o servidor dispara sozinho no timer dele,
client nunca manda mensagem nenhuma nesse branch) e mover o desconto
REAL pra `client/remote_entity_handlers.py::_apply_combat_result`, no
mesmo bloco que já cria a flecha visual (`_spawn_archer_auto_arrow`),
condicionado a `damage > 0` (mesma regra do servidor: miss não consome).
Agora desconto de flecha e flecha aparecendo são **literalmente o mesmo
evento** — não tem mais como divergir.

Validado: `py_compile` dos dois arquivos; suíte completa sem regressão
(7F/85P, mesma baseline). Não dá pra escrever um teste headless pra isso
(é lógica de `ui/systems.py`/cliente pygame, não do servidor) — a
evidência é a contagem exata extraída do log real do usuário (52 vs 29
vs 15). **Não validado:** sessão manual confirmando que a contagem da
aljava no HUD nunca mais diverge do número de flechas que realmente
saíram.

---

### 21.4 Correção: miss/dodge/parry TAMBÉM consomem flecha (10/07/2026)

**Reportado pelo usuário:** a 21.3 descreveu (e manteve) o comportamento
pré-existente de só consumir flecha em hit/crit como se fosse a regra
correta a espelhar no cliente. Está errado — fisicamente, se a flecha
saiu do arco (o servidor autorizou e resolveu o tiro), ela foi gasta,
não importa se acertou ou errou. Miss consumir "de graça" nunca fez
sentido e nunca foi intencional, era só onde o `return` antigo cortava
o fluxo antes de chegar no bloco de consumo.

**Fluxo correto (conforme o usuário):** alvo em condição de ser atacado
→ servidor autoriza → client gera projétil + som + FLT + desconta
aljava. Servidor não autoriza (fora de alcance/sem LOS/sem
perseguição/sem munição — Decisão 21.1) → espera o próximo tick em que
as condições se repetem, sem nenhum efeito colateral. Binário: autorizou
e resolveu (consome, seja qual for o resultado) ou não autorizou (nada
acontece, tenta de novo depois).

**Fix:**
- `server/spell_completion_processor.py::_server_apply_ranged_physical`:
  o bloco de consumo de flecha foi movido pra **logo depois do
  hit-roll** (`resolve_attack_outcome`), **antes** do
  `if outcome in ("miss","dodge","parry"): return`. Agora consome em
  hit/crit/block E TAMBÉM em miss/dodge/parry — qualquer resultado que
  passou pelas checagens de autorização (alvo vivo, não evadindo, LOS
  ok) consome. Só os retornos ANTERIORES ao hit-roll (alvo morto,
  `_is_evading` → outcome `"evade"`, LOS falhando dentro da própria
  função — rede de segurança só relevante pra skills, auto-attack já é
  barrado antes disso pela Decisão 21.1) continuam sem consumir, porque
  nesses casos o tiro nem chega a ser autorizado/resolvido de verdade.
- `client/remote_entity_handlers.py::_apply_combat_result`: a condição
  de desconto local (adicionada na 21.3) mudou de `damage > 0` pra
  `outcome != "evade"` — desconta em qualquer resultado real (incluindo
  miss/dodge/parry/immune), só não desconta no caso "evade" (o único
  outcome de "não autorizado" que ainda assim chega ao cliente via
  COMBAT_RESULT, já que `combat_processor.py` sempre despacha o
  resultado de `_server_apply_ranged_physical`, autorizado ou não).

Validado: `py_compile`; suíte completa sem regressão (7F/85P, mesma
baseline); script headless novo (`test_miss_consumes_arrow.py`, força
`acerto=0` e confirma que a aljava desconta mesmo com HP do alvo
inalterado) confirma que miss consome flecha no servidor.
**Não validado:** sessão manual confirmando a paridade visual completa
(flecha aparece + aljava desconta juntos, inclusive em erro) e o caso
"evade" (mob em RETURNING) não descontando.

---

### 22. Validação de MOVE trocada de lockstep exato pra orçamento tempo×velocidade (11/07/2026)

**Reportado pelo usuário (tester em rede real, não localhost):** um player
ficava com o personagem "desincronizado" — se movia normal na tela dele,
mas pros outros players ficava parado, e nem mobs agravam nele (a IA de
mob lê a posição real do servidor — se ela nunca atualiza, nunca detecta
proximidade).

**Causa raiz:** `WorldServer.move_player()` exigia que todo `MOVE`
estivesse a EXATAMENTE 1 tile da última posição CONFIRMADA pelo servidor
— senão rejeitava (`server/world_server.py`, checagem antiga `dx>1 or
dy>1`). Um motivo comum e nada exótico de rejeição (ex.: tile
temporariamente ocupado por outro player/mob cruzando o caminho) já
bastava pra travar; e a rejeição SÓ se autocorrigia
(`client/network_handlers.py::_handle_msg_entity_move`) quando o player
estava parado ou dando dash — andando normal, a correção de posição era
**deliberadamente ignorada** (comentário explícito no código: evitar que
uma correção desatualizada pelo delay de rede sobrescrevesse um passo
legítimo mais recente). Combinado, isso é auto-alimentado: 1 rejeição →
client nunca aprende a posição real → todo MOVE seguinte, calculado
relativo à posição LOCAL (que só diverge mais), também é rejeitado → trava
permanente, só resolvida saindo e voltando da área de visão ou relogando.

**Pesquisa (a pedido do usuário) — como MMOs consolidados resolvem isso:**
o modelo do WoW (documentado no wiki da TrinityCore, que reimplementa o
protocolo original) é cliente autoritativo pra posição — servidor não
exige repetição exata, só valida **plausibilidade**: alcançável dado
tempo decorrido × velocidade, com folga generosa em rajada de lag. Troca
consciente (menos rígido contra cheat, mas sem travar jogador legítimo) —
adotada aqui na mesma linha, dado que é um teste alpha pequeno, não uma
produção com milhares de estranhos.

**Fix — `WorldServer.move_player()` não exige mais adjacência exata:**
- Novo baseline por player em `TileMovement` (`_last_valid_tile_x/y`,
  `_last_valid_ts` — `engine/components.py`): última posição de confiança
  + timestamp REAL do servidor (`time.time()`, nunca o `ts` do payload do
  cliente — evita um cliente modificado inflar o próprio orçamento
  mentindo sobre o tempo).
- Aceita qualquer destino dentro do orçamento `tempo_decorrido ×
  velocidade × MOVE_SPEED_TOLERANCE` (tolerância=2.0, teto de crédito
  `MOVE_ELAPSED_CAP_S=2.0` pra quem ficou parado/AFK não acumular
  orçamento infinito) — pega speedhack/teleporte em chão aberto.
- Caminho reto (última posição confirmada → destino) não pode cruzar tile
  sólido — reusa `EnemyAISystem._has_line_of_sight` (mesmo Bresenham do LOS
  do arqueiro) como "checagem de parede no meio do caminho" — pega
  teleporte por cima de parede que passaria só pelo check de distância.
- Se passar nos dois: servidor **adota a posição reportada direto** (não
  precisa mais de cadeia ininterrupta de confirmações) — isso sozinho já
  elimina a trava permanente, porque o servidor "alcança" onde o cliente
  legitimamente está, em vez de exigir que nunca tenha havido nenhuma
  rejeição no meio do caminho.
- Baseline resincronizada em TODO ponto que escreve a posição real — `snap_to_tile()` (`engine/utils.py`, cobre knockback/
  teleporte/respawn) e a finalização do tween em
  `TileMovementSystem.update()` (`engine/world_systems.py`, cobre o dash
  do Interceptar, que usa o MESMO tween só com `move_duration` curto) —
  sem isso, o PRIMEIRO `MOVE` normal logo depois de um Tiro Repulsivo ou
  Interceptar pareceria um salto implausível comparado à posição
  pré-deslocamento congelada. Nem Interceptar nem Tiro Repulsivo passam
  por `move_player()` diretamente (ambos já usavam `snap_to_tile`/tween
  próprio antes desta mudança) — o único ponto de contato é essa
  resincronização de baseline.
- `client/network_handlers.py::_handle_msg_entity_move`: a correção de
  posição andando normal (que antes era sempre ignorada) agora É aplicada
  quando o gap é maior que 1 tile — um gap de 1 tile continua sendo
  tratado como "correção desatualizada, ignora" (raciocínio original
  preservado pro caso comum), mas um gap maior só acontece hoje por
  rejeição REAL (fora do orçamento ou atravessou parede — já não é mais o
  caso comum de colisão dinâmica transitória), então vale a pena aplicar
  mesmo andando, como rede de segurança final.

Validado: `py_compile` de todos os arquivos tocados; suíte completa sem
regressão (7F/85P, mesma baseline); script headless novo
(`test_move_validation.py`) confirma os 5 casos: (1) move normal de 1
tile aceito, (2) salto de 20 tiles sem tempo decorrido rejeitado, (3) —
**o caso que reproduz o bug relatado** — um move de 1 tile logo após uma
rejeição anterior agora é ACEITO (no modelo antigo, ficaria travado pra
sempre), (4) pulo por cima de parede rejeitado mesmo dentro do orçamento
de distância/tempo, (5) move normal aceito imediatamente após
`snap_to_tile` (knockback), sem falso-positivo de salto implausível.

**Não validado:** sessão manual com testers em rede real confirmando que
o desync não volta a ocorrer (o bug original só era reproduzível em rede
real, nunca em localhost/dev — mesma limitação do bug "player remoto
congela" de 06/07/2026, ver `PROBLEMAS_ARQUITETURA.md`); passada manual
específica com Interceptar/Tiro Repulsivo em sequência rápida com
movimento normal, pra confirmar na prática que a resincronização de
baseline evita falso-positivo.

---

### 23. Sistema de ícones de mapa/minimapa — morte, quests, treinadores, mercadores (11/07/2026)

**Pedido do usuário:** ao morrer, o player não tinha como saber onde no
mapa ficou seu corpo/espírito — "o player teria que lembrar onde morreu".
Pedido inicial era só um ícone de morte no mapa (M) e minimapa; o usuário
então pediu pra generalizar num sistema, já prevendo quest givers
(disponível/em progresso/completável), treinadores (ícone por classe) e
mercadores — a maioria dos ícones ainda não existe, então o sistema
precisa de fallback (círculo + símbolo) até os arquivos chegarem.

**Fonte única de estado, reaproveitada em 3 lugares (nunca duplicada):**
`QuestDialogSystem.marker_for(npc_id)` (`ui/quest_system.py`) — extraído
da lógica que já existia em `render_world()` (indicador acima da cabeça do
NPC) — retorna `(icon_name, cor_fallback, símbolo_fallback)` ou `None`.
Usado por `render_world()` (mundo) E por `ui/map_markers.py::collect_markers`
(mapa/minimapa). Estados (mantido igual ao que já existia no indicador
acima da cabeça — usuário confirmou depois de eu apontar a diferença que
tinha proposto por engano): disponível=dourado **!**, em progresso=cinza
**?**, completável=dourado **?**, bloqueada por nível=cinza **!**.

**`ui/map_markers.py`** (novo): `MapMarker(tile_x, tile_y, icon_name,
fallback_color, fallback_symbol)` + `collect_markers(world, player_entity,
quest_dialog)`. Fontes: `GhostState.corpse_tx/ty` do player local (morte —
única exceção que NÃO filtra por `Visible`, é a posição já conhecida do
próprio player, sempre mostrada enquanto o corpo existir); `QuestGiver`/
`Trainer`/`Merchant`, todos filtrados pelo componente `Visible` (mesma tag
dinâmica de FoW que já gate os indicadores acima da cabeça — mesma regra,
sem duplicar).

**Ícones**: convenção `map_<algo>.png` em `assets/icons/` (mesmo padrão
`skill_`/`item_`/`enemy_` já usado por `IconManager`/`ICONS`, sem pasta
nova — `assets/` é o único lugar de asset do projeto, `ui/` é código).
`death.png`/`death.ase` renomeados pra `map_death.png`/`map_death.ase`.
Arquivos que ainda faltam (usuário vai providenciar): `map_quest_available`,
`map_quest_inprogress`, `map_quest_complete`, `map_quest_locked`,
`map_trainer_guerreiro`, `map_trainer_mago`, `map_trainer_arqueiro`,
`map_merchant`. `ICONS.get()` já retorna `None` de forma graciosa pro que
não existe — `MapOverlay.render()`/`Minimap.render()` caem pro fallback
(círculo colorido + glifo) automaticamente, sem nenhum código condicional
extra por marcador — assim que o arquivo aparecer em `assets/icons/`, o
ícone substitui o fallback sozinho, nos 3 lugares (mundo, mapa, minimapa).

**Trainer (`ui/trainer_system.py::render_world`)**: mesma troca —
ícone `map_trainer_{class_id}` primeiro, fallback letra "T" (comportamento
anterior, preservado).

**`MapOverlay.render()`/`Minimap.render()`**: parâmetro único `corpse_tile`
(implementação inicial, só morte) generalizado pra `markers: list[MapMarker]`
antes de eu terminar de conectar em `game.py` — os dois métodos de desenho
não sabem nada sobre tipos de marcador, só iteram a lista.

**Limitação conhecida (RESOLVIDA na Decisão 25 — 11/07/2026):** o mapa
grande (M) usava o mesmo `collect_markers` do minimapa, filtrado por
`Visible` (visão atual) — um quest giver fora do campo de visão atual não
aparecia. Ver Decisão 25: `collect_markers` parou de filtrar por
`Visible` de propósito, isso deixou de ser limitação.

Validado: `py_compile` de todos os arquivos tocados; suíte completa sem
regressão (7F/85P, mesma baseline); smoke test confirma `map_death.png`
carrega via `ICONS.get()` e um ícone inexistente retorna `None` (aciona o
fallback) como esperado.

**Não validado:** sessão manual no jogo confirmando visualmente o ícone de
morte no mapa/minimapa após morrer, e o fallback (círculo+símbolo) nos
demais marcadores até os ícones reais chegarem.

---

### 23.1 Ajustes: ícones não sobrepõem, tamanho fixo (não escala com zoom) (11/07/2026)

**Pedido do usuário:** dois problemas na implementação inicial da Decisão
23 — (1) marcadores no mesmo tile (ou próximos) ficavam desenhados um em
cima do outro; (2) o tamanho do ícone no mapa grande crescia/encolhia
junto com o zoom (scroll), e no minimapa não tinha um tamanho "correto"
definido. Usuário vai padronizar os ícones-fonte em 8×8px.

**Fix — desconflito de posição:** `ui/map_markers.py::deconflict_positions(
points, min_dist)` — recebe centros já em coordenada de TELA (não tile) e
devolve a mesma lista com qualquer posição que colidiria (a menos de
`min_dist` de outra já resolvida) empurrada em busca espiral (8 direções,
raio crescente) até achar um slot livre. Puramente geométrico, não sabe
nada sobre tipo de marcador — `MapOverlay.render()` e `Minimap.render()`
chamam isso ANTES de desenhar, com `min_dist = icon_size * 0.9`.

**Fix — tamanho fixo:** `icon_size` deixou de derivar de `scale` (mapa
grande) ou `tp` (minimapa) — agora é `self._u(20)` (mapa) / `self._u(12)`
(minimapa), só reage à "Escala da UI" do menu de pausa (mesmo padrão do
resto da HUD), nunca ao zoom/scroll do mapa nem ao tamanho de tile do
minimapa. `IconManager` já escala o ícone-fonte (8×8 planejado) pro
tamanho de destino via nearest-neighbor, então o tamanho de exibição é
sempre o mesmo independente da resolução do arquivo.

Validado: `py_compile`; suíte completa sem regressão (7F/85P); smoke test
novo confirma 3 marcadores no MESMO ponto saírem com distância >= `min_dist`
entre todos os pares depois de `deconflict_positions`.

**Não validado:** sessão manual confirmando visualmente que os ícones não
se sobrepõem em campo (vários NPCs próximos) e que o tamanho fica estável
em qualquer zoom do mapa grande.

---

### 23.2 Correção: 8px de verdade (não upscale), e indicador acima da cabeça só pra quest (11/07/2026)

**Reportado pelo usuário:** a 23.1 "esticou uma sprite de 8px pra 32px" —
`self._u(20)`/`self._u(12)` não é "tamanho fixo que não escala com zoom",
é "tamanho fixo que escala com a Escala da UI" — não era o que o usuário
queria. Pedido real: ícone do tamanho REAL do arquivo-fonte (8×8), sem
upscale nenhum, em qualquer resolução de mapa/minimapa. Além disso, o
usuário decidiu que o indicador acima da cabeça do NPC (mundo do jogo) só
faz sentido pra quest givers — treinador não precisa (a letra "T" que já
existia é suficiente).

**Fix:**
- `MapOverlay.render()`/`Minimap.render()`: `icon_size` virou uma
  constante literal `8` (sem `self._u()` nenhum) — pedido pra
  `deconflict_positions` ajustado junto (`icon_size * 0.9`).
- `ui/trainer_system.py::render_world`: revertido pro comportamento
  original (só letra "T", nunca tenta ícone) — ícone por classe
  (`map_trainer_*`) continua existindo, só que exclusivamente pro
  mapa/minimapa (`ui/map_markers.py`).
- `ui/quest_system.py::QuestDialogSystem.render_world`: mantém o ícone
  (usuário confirmou via pergunta direta: quer o ícone acima da cabeça do
  quest giver, só que do tamanho certo) — mas `world_surf` é desenhada em
  coordenada LÓGICA (`screen/zoom`, ver `game.py` — só escalada pro
  tamanho real da tela DEPOIS de tudo desenhado), então pedir `8` direto
  pro `IconManager` ali dava 8px lógicos, que viravam `8*zoom` px reais de
  tela — daí o "ainda 32x32" (zoom da câmera do jogo, não tem relação com
  o zoom do mapa M). Fix: `icon_size = max(1, round(8 / zoom))`, com
  `zoom` agora passado como 3º parâmetro de `render_world()` (novo,
  `game.py` passa `self._zoom`) — resultado sempre 8px reais de tela,
  qualquer nível de zoom da câmera.

Validado: `py_compile`; suíte completa sem regressão (7F/85P, mesma
baseline).

**Não validado:** sessão manual confirmando visualmente 8px reais nos 3
lugares (mapa, minimapa, acima da cabeça do quest giver) em pelo menos 2
níveis de zoom da câmera diferentes.

---

### 23.3 Nome flutuante acima de NPCs e mobs (substitui a letra "T") (11/07/2026)

**Pedido do usuário:** tirar a letra "T" do treinador; todo NPC (mercador,
treinador, quest giver, ferreiro) deve mostrar o próprio nome acima da
cabeça, e o mesmo padrão vale pra mobs. Ícone de quest (23.2) continua
existindo, mas agora fica ACIMA do nome, não no lugar dele.

**Fix:**
- `ui/systems.py::RenderSystem` — novo `_name_font` (14px, "tamanho que
  achei razoável", ajustável) + `NAME_OFFSET_Y=14`. Dois pontos no loop
  principal de entidades: (1) qualquer entidade com `NPC` e SEM barra de
  HP desenha `NPC.name` na âncora `draw_y - height/2 - NAME_OFFSET_Y`
  (mesma posição que a letra "T"/indicador de quest usavam antes); (2)
  qualquer entidade com `EntityIdentity` e HP bar, que NÃO seja
  `PlayerControlled`/`RemoteControlled` (ou seja, mob local/offline),
  desenha o nome logo acima da própria barra de HP. Cobre NPCs e mobs
  locais (usados em modo single-player/teste headless).
- `client/remote_entity_handlers.py::_draw_mob_hp_bars` — mob remoto
  (online, servidor autoritativo) não passa pelo `RenderSystem` acima
  (renderizado à parte, ver `_spawn_remote_mob`), então o mesmo desenho de
  nome foi espelhado aqui, usando o `EntityIdentity.name` já setado no
  spawn (nome próprio do servidor, ex. "Boneco de treino", ou derivado da
  raça).
- `ui/trainer_system.py::render_world`: letra "T" removida — treinador não
  tem mais NENHUM indicador próprio acima da cabeça (o nome já cobre isso,
  via `RenderSystem`).
- `ui/quest_system.py::render_world`: âncora do ícone (`sy`) deslocada pra
  cima em `14 (NAME_OFFSET_Y) + 18 (altura aprox. do nome) + 4 (respiro)` —
  números fixos, não lê o valor real do outro arquivo (simplicidade,
  suficiente porque só precisa ser "generoso o bastante" pra não
  sobrepor).

Validado: `py_compile`; suíte completa sem regressão (7F/85P, mesma
baseline).

**Não validado:** sessão manual confirmando visualmente nome acima de
cada tipo de NPC/mob (local e remoto) e ícone de quest não sobrepondo o
nome.

---

### 23.4 Fix: "T" ainda aparecia, fonte ilegível (11/07/2026)

**Reportado pelo usuário:** dois problemas na 23.3 — (1) a letra "T" do
treinador continuava aparecendo (a 23.3 só tirou a TENTATIVA de ícone,
mas manteve o fallback de letra por engano — nunca virou de fato um
no-op); (2) fonte do nome ilegível — **causa raiz**: `self._name_font =
CachedFont(None, 14)` usava `None` como caminho, ou seja `pygame.font.
Font(None, 14)` = fonte PADRÃO do pygame, não a fonte pixelizada do
projeto (`ui/fonts.py::make()`, Determination) — nunca estava carregando
nada do projeto.

**Fix:**
- `ui/trainer_system.py::render_world`: corpo inteiro virou `pass` (mesmo
  padrão já usado pro indicador "LOJA" do mercador, removido antes por
  motivo idêntico).
- `ui/fonts.py`: nova `make_pixel(size=10)`, carrega
  `assets/fonts/MEGAMAN10.ttf` (fonte pixel, pedida pelo usuário pra
  TESTAR em paralelo com a Determination — não substitui a fonte do
  projeto inteiro, só os labels de nome por enquanto) — sem `_SCALE`
  (fontes bitmap já vêm na grade certa; aplicar correção de métrica
  desenharia errado). Segue a mesma regra de pixel-perfect do resto do
  projeto: `CachedFont.render()` default é `antialias=False`.
- `ui/systems.py::RenderSystem`/`client/remote_entity_handlers.py::_draw_mob_hp_bars`:
  `_name_font`/`_mob_name_font` trocados de `pygame.font.Font(None, ...)`
  pra `ui.fonts.make_pixel()`.

Validado: `py_compile`; smoke test confirma a fonte carrega de
`assets/fonts/MEGAMAN10.ttf` (não cai no fallback) e renderiza texto;
suíte completa sem regressão (7F/85P, mesma baseline).

**Não validado:** sessão manual confirmando visualmente que o "T" sumiu
de vez e que MEGAMAN10 fica legível no tamanho nativo (10) — se não
ficar bom o suficiente, é só chamar `make_pixel(outro_tamanho)`.

---

### 23.5 Causa raiz real do "ilegível": zoom da câmera reamostrava a fonte (11/07/2026)

**Reportado pelo usuário (com screenshot):** mesmo depois da 23.4 (fonte
certa carregada), o nome continuava ilegível — vários "Boneco de treino"
sobrepostos numa sopa de letra. Pedido explícito: mostrar a fonte no
TAMANHO DELA, sem o zoom da câmera alterar esse tamanho, "pois isso
distorce a mesma".

**Causa raiz:** todo o mundo (`ui/systems.py`, `ui/quest_system.py`,
`client/remote_entity_handlers.py`) desenha numa surface LÓGICA menor
(`world_surf`/`zoom_surf`, tamanho `tela/zoom`) que só DEPOIS é escalada
pro tamanho real da tela via `pygame.transform.scale()` (`game.py`, passe
final do frame). Isso é perfeito pra sprites/tiles (pixel art desenhada
num grid, escala bem), mas texto desenhado a partir de um TTF nesse
espaço lógico é rasterizado UMA VEZ no tamanho lógico e depois
redimensionado por um fator não-inteiro (o zoom) — a fonte nunca aparece
no tamanho que o rasterizador desenhou de verdade, sempre borrada/
distorcida pelo resize. A 23.4 corrigiu QUAL fonte carregar, mas não
onde ela era desenhada — continuava passando pelo mesmo resize.

**Fix — `ui/world_labels.py`** (novo): fila `WORLD_LABELS` que recebe
posição de MUNDO + texto/ícone durante o passe de mundo mas só desenha
DEPOIS do `pygame.transform.scale()` já ter rodado, direto em
`self.screen` — nasce no pixel final da tela, nunca é reamostrado.
Empilha por `stack_key` (entity_id): nome primeiro, ícone de quest por
cima, respiro fixo de 4px de TELA (não escala com zoom, de propósito).
- `ui/systems.py::RenderSystem` — nome de NPC e nome de mob local
  enfileiram em vez de desenhar direto no `world_surf`.
- `client/remote_entity_handlers.py::_draw_mob_hp_bars` — mesma troca pro
  nome do mob remoto (online).
- `ui/quest_system.py::render_world` — ícone (ou fallback círculo+glifo,
  agora numa Surface própria cacheada por cor+símbolo, pra poder entrar
  na fila igual um ícone de verdade) enfileira com o MESMO `stack_key`
  do NPC, empilhando por cima do nome automaticamente. `icon_size` volta
  a ser `8` literal — a compensação `8/zoom` da 23.2 não é mais
  necessária (o ícone nunca mais passa pela escala do mundo).
- `game.py`: `WORLD_LABELS.render(self.screen, cam_x, cam_y, z)` chamado
  uma vez, logo depois do `pygame.transform.scale()` do mundo.

Validado: `py_compile`; smoke test da fila (add_text/add_icon/render sem
erro, fila esvazia sozinha); suíte completa sem regressão (7F/85P, mesma
baseline).

**Validado visualmente pelo usuário** (11/07/2026, com screenshot): nome
nítido e estável, sem borrão do zoom — confirma a causa raiz (reamostragem
no `transform.scale`, não a fonte em si). Único ajuste necessário depois
disso foi tamanho: `make_pixel()` default subiu de 10 (nome do arquivo)
pra 16px — 10 era pixel-perfect mas pequeno demais pra ler em jogo; como
agora o tamanho é puramente estético (nunca mais reamostrado), é só
questão de escolher um valor confortável, sem risco de distorcer.

---

### 23.6 Nome de player (local + remoto) via WORLD_LABELS, com círculo de nível (11/07/2026)

**Pedido do usuário:** mesmo tratamento pixel-perfect (23.5) pro nome dos
PLAYERS (não só NPC/mob), e um círculo com o nível à esquerda do nome.

**Achado:** player remoto JÁ tinha nome desenhado
(`client/remote_entity_handlers.py::_draw_remote_players`), mas do jeito
antigo — direto no `zoom_surf` via `self.font_xs`, sofrendo o mesmo
borrão do zoom que os NPCs/mobs tinham antes da 23.5. Nível de player
remoto não existia no cliente — `RemoteControlled` não tinha o campo,
apesar do SERVIDOR já mandar `"level"` no payload de spawn há tempos
(`server/session.py`, 3 pontos: `WORLD_STATE`/`ENTITY_SPAWN`/reconexão) —
o cliente só nunca lia.

**Fix:**
- `engine/components.py::RemoteControlled`: novo campo `level: int = 1`.
- `client/remote_entity_handlers.py::_spawn_remote_player_entity`: lê
  `data.get("level", 1)` (dado que o servidor já mandava).
- `ui/world_labels.py::build_name_row(font, name, level, ...)` (novo):
  monta círculo+número e nome lado a lado numa Surface só, MESMA fonte
  pixel-perfect pro número e pro nome — entra em `WORLD_LABELS.add_icon()`
  como um item único (nunca separa nome do círculo na pilha).
- `_draw_remote_players`: nome antigo trocado por
  `WORLD_LABELS.add_icon(..., build_name_row(...))`.
- `ui/systems.py::RenderSystem`: novo bloco pro player LOCAL
  especificamente (`PlayerControlled`, usa `CharacterStats.name/.level` —
  fonte sempre atualizada, ao contrário de `EntityIdentity.level` que
  nunca é tocado depois do spawn) — `elif` do bloco de nome de mob, pra
  não desenhar 2x um player remoto (esse já é tratado em
  `_draw_remote_players`, que roda por fora deste loop).

Validado: `py_compile`; smoke test de `build_name_row` (gera Surface sem
erro, dimensões consistentes); suíte completa sem regressão (7F/85P,
mesma baseline).

**Não validado:** sessão manual confirmando visualmente nome+nível do
próprio player e de players remotos (precisa de 2+ contas pra testar o
caso remoto).

---

### 23.7 HUD de barras com asset próprio (nível+XP+HP+recurso) — substitui a barra retangular (11/07/2026)

**Pedido do usuário:** dois assets desenhados à mão
(`assets/hud/player_hud_bar.png` 64×16, `assets/hud/mob_hud_bar.png`
48×12) — quadrado de nível à esquerda + barras de XP/HP/recurso (player)
ou só HP (mob) — pra SUBSTITUIR a barra retangular simples de sempre (só
o preenchimento, o fundo/trilho já vem no asset). Pergunta em aberto do
usuário: como centralizar o preenchimento em cima do asset, como validar,
e como evitar distorção (o asset foi desenhado com base no grid de tile
do jogo, 32×32).

**Mapeamento de coordenadas — nunca por olho:** RLE (run-length encoding)
de cada linha dos dois PNGs, depois validado com uma imagem de debug
(contorno colorido sobre o asset ampliado 8x nearest-neighbor) antes de
escrever qualquer código de jogo. Coordenadas nativas resultantes em
`ui/hud_bars.py` (`P_LEVEL_BOX`, `P_BAR_X0/X1`, `P_XP_Y`/`P_HP_Y`/
`P_RES_Y` pro player; `M_LEVEL_BOX`, `M_HP_X0/X1`, `M_HP_Y` pro mob).

**Decisão de "sem distorção" — duas camadas em espaços diferentes:**
- **Fundo do asset + barras de preenchimento** (bloco de cor sólida, sem
  detalhe fino): ESPAÇO DE MUNDO (`world_surf`/`zoom_surf`), tamanho
  NATIVO do arquivo, SEM escala extra nenhuma. Já que o usuário desenhou
  o asset no grid de tile (32×32) de propósito, ficando em espaço de
  mundo a HUD escala junto com o sprite/zoom da câmera exatamente como
  toda pixel art do jogo já faz — zero distorção RELATIVA ao personagem
  que carrega (é a mesma pipeline de sprite/tile, nunca reclamada de
  "borrada"; só texto fino sofre visivelmente com resample de zoom não-
  inteiro, ver 23.5).
- **Número do nível + nome**: texto fino — esse sim vai por
  `ui/world_labels.py::WORLD_LABELS` (screen-space, pós-zoom), senão
  ficaria borrado que nem o nome ficava antes da 23.5. Novo
  `add_text_centered()`/`add_icon_centered()` (posição exata, sem pilha)
  pro número do nível; `add_text()`/`add_icon()` ganharam `gap_before`
  (px de tela) pra suportar o pedido específico "nome 2px acima da HUD"
  sem quebrar o gap padrão (4px) usado pelo ícone de quest acima do nome
  de NPC.

**Cores** (`ui/hud_bars.py`): HP verde `(0,200,60)` (igual já era), XP
roxo claro `(190,140,230)` (pedido do usuário), recurso por classe
reaproveitando as MESMAS cores já usadas no HUD lateral
(`client/hud_handlers.py`) — mana `(50,100,255)`, concentração
`(80,160,220)`, raiva `(255,160,0)`.

**`ui/hud_bars.py`** (novo): `draw_player_hud()`/`draw_mob_hud()` —
desenham fundo+preenchimento em `world_surf` e devolvem DELTAS (não
posição absoluta) relativos ao ponto de entrada, pro caller somar com a
posição de MUNDO (não a lógica/deslocada-de-câmera) da entidade e
alimentar `WORLD_LABELS` corretamente.

**Substituições** (removeu o retângulo simples fundo+preenchimento de
vez, "o fundo já tem na hud"):
- `ui/systems.py::RenderSystem` — player LOCAL (`PlayerControlled`) usa
  `draw_player_hud` com XP/recurso reais (`CharacterStats`); mob local/
  offline usa `draw_mob_hud`. Player remoto (PvP) SAIU desta função de
  vez (antes tinha um branch `RemoteControlled`/`_rc_hp` aqui) — foi pra
  `_draw_remote_players`, senão desenharia 2x.
- `client/remote_entity_handlers.py::_draw_remote_players` — reescrita
  completa: `draw_player_hud` com XP/recurso zerados (player remoto não
  expõe esse dado pro cliente, só o dono vê o próprio — linhas ficam só
  com o trilho vazio do asset, sem preenchimento).
- `client/remote_entity_handlers.py::_draw_mob_hp_bars` — `draw_mob_hud`
  no lugar do retângulo antigo; nome/nível/status-icons reposicionados
  pro novo topo da HUD (mais alta que a barra antiga de 4px).

Validado: `py_compile` de todos os arquivos tocados; suíte completa sem
regressão (7F/85P, mesma baseline); **teste end-to-end real** — `World`
com player+mob de verdade, `RenderSystem.render()` + `WORLD_LABELS.render()`
executados sem exceção, screenshot capturada e conferida visualmente
(nível no quadrado, barras nas proporções certas — HP 70%, XP 40%, raiva
55% no player; HP 40% no mob —, nome com o espaçamento pedido).

**Não validado:** sessão manual dentro do jogo de verdade (o teste
end-to-end usou entidades sintéticas, não o fluxo completo de spawn/
rede); confirmação visual do caso remoto (precisa 2+ contas).

---

### 23.8 Ícones de efeito (bleed/stun/sleep...) migram pra fila à direita da HUD (11/07/2026)

**Pedido do usuário:** ícones de efeito ativo deixam de ficar centralizados
acima da barra de HP — vão pra uma fila horizontal à DIREITA da HUD nova
(23.7). Comportamento de fila: o mais antigo fica mais perto da HUD, cada
novo efeito entra na ponta direita; quando um expira, o efeito novo
"entra no lugar do anterior" (não fica pulando posição).

**Achado — já era assim, só precisava mudar a âncora:** `_draw_effect_icons`
já iterava `active_effects` (vindo de `StatusEffects.effects`, um dict —
Python preserva ordem de inserção) e desenhava cada ícone
`ICON+GAP` px à direita do anterior, recalculado do zero a cada frame. Ou
seja, a fila "estilo WoW" (mais antigo primeiro, reflow automático quando
um expira, novo sempre na ponta) já existia — só faltava trocar de onde
ela começa a desenhar: era centralizada acima da barra (`draw_x -
total_w/2`, `bar_y - ICON - 10`), virou a partir da borda direita da HUD
(`start_x`, `center_y`).

**Fix:**
- `ui/hud_bars.py::draw_player_hud`/`draw_mob_hud` — retorno estendido de
  3 pra 5 deltas: `(dx_level, dy_level, dy_top, dx_right, dy_center)` —
  os 2 novos são a borda direita e o meio vertical da HUD.
- `ui/systems.py::_draw_effect_icons` — assinatura trocou de
  `(draw_x, bar_y)` pra `(start_x, center_y)`; ícones desenham a partir
  daí crescendo pra direita, centralizados verticalmente (antes:
  centralizados horizontalmente, crescendo a partir do centro).
- Os 2 call sites (`ui/systems.py::RenderSystem`,
  `client/remote_entity_handlers.py::_draw_mob_hp_bars`) passam
  `draw_x + dx_right, draw_y + dy_center` (valores devolvidos por
  `draw_player_hud`/`draw_mob_hud` no mesmo frame) em vez do `bar_y`
  antigo.

Validado: `py_compile`; suíte completa sem regressão (7F/85P, mesma
baseline); **teste end-to-end real** — `World` com player + 3 efeitos
ativos (bleed/stun/sleep, adicionados nessa ordem), `RenderSystem.render()`
+ `WORLD_LABELS.render()`, screenshot conferida visualmente: os 3 ícones
aparecem em fila à direita da HUD, bleed (inserido primeiro) mais perto,
sleep (inserido por último) mais à direita — confirma a ordem e o reflow
automático.

**Não validado:** sessão manual dentro do jogo de verdade confirmando o
reflow ao vivo (um efeito expirando enquanto outros continuam ativos).

---

### 23.9 Causa raiz do desalinhamento: fundo+número da HUD em dois espaços de escala diferentes — composição única (11/07/2026)

**Reportado pelo usuário (com screenshot):** a barra de HP de um boneco de
treino aparecia ACIMA da HUD e ABAIXO do número do nível — nem dentro do
quadrado, nem alinhada com o resto. Pedido: agrupar HUD+barra+nível (e o
mesmo pros players) pra nunca mais ficar fora de ordem.

**Causa raiz:** a 23.7/23.8 desenhavam fundo+barras em ESPAÇO DE MUNDO
(escala com o zoom da câmera) e número do nível em ESPAÇO DE TELA via
`WORLD_LABELS` (tamanho fixo, nunca escala — pixel-perfect de propósito).
Dois sistemas de escala DIFERENTES pro mesmo elemento visual: um número de
2 dígitos ("99", nível de boneco de treino) não cabia no quadrado nativo
de só 12px de largura do asset do mob, e "vazava" pra fora — parecia
flutuar desconectado da HUD, exatamente como reportado.

**Fix — unificar tudo no mesmo espaço, de vez:** `ui/hud_bars.py`
reescrito — `build_player_hud()`/`build_mob_hud()` agora montam fundo +
barras + número do nível numa ÚNICA Surface, com um upscale fixo
(`SCALE=3`, nearest-neighbor — não é "distorção", é ampliação de pixel
art, só existe pra o número de 2 dígitos caber na caixinha; câmera não
influencia esse fator). Essa Surface inteira vai pro `WORLD_LABELS` como
UM ícone só (`add_icon`, mesmo `stack_key` do nome) — impossível
desalinhar arte de número, porque nascem juntos no mesmo pixel da mesma
Surface. Efeitos ativos passaram pelo mesmo tratamento: `_draw_effect_icons`
virou `_build_effects_row()` (monta a fila numa Surface própria) +
`ui/world_labels.py::add_icon_offset()` (novo — projeta a posição pra tela
e soma um deslocamento em PX DE TELA fixo com alinhamento de borda,
`ui/hud_bars.py::effects_row_offset()` calcula o deslocamento certo a
partir do tamanho conhecido da Surface da HUD) — senão a fila
desalinharia da HUD do mesmo jeito conforme o zoom mudasse.

Removido: `draw_player_hud`/`draw_mob_hud` (retornavam deltas pra dois
espaços diferentes) e `WORLD_LABELS.add_text_centered`/`add_icon_centered`
(só existiam pra esse caso, agora sem uso).

Validado: `py_compile`; suíte completa sem regressão (7F/85P, mesma
baseline); **teste end-to-end reproduzindo o cenário exato do screenshot**
— player + 3 bonecos de treino próximos, nível 99 nos dois tipos de HUD,
efeito ativo, floating text — screenshot conferida: "99" cabe dentro dos
dois quadrados (player e mob) sem vazar, barras alinhadas com o fundo,
fila de efeito à direita funcionando.

**Limitação conhecida, NÃO resolvida agora** (visível no próprio teste de
validação): quando várias entidades ficam muito próximas (ex: grade de
bonecos de treino lado a lado, como no screenshot original), os NOMES de
entidades DIFERENTES ainda se sobrepõem entre si — é um problema
DIFERENTE do que foi corrigido aqui (esse era desalinhamento DENTRO da
HUD de uma única entidade; aquele é colisão ENTRE HUDs de entidades
vizinhas). A mesma técnica de `ui/map_markers.py::deconflict_positions`
(23.1, já usada no mapa/minimapa) resolveria — não implementado ainda
porque não foi pedido nesta rodada.

---

### 23.10 Floating text nasce na base do personagem (11/07/2026)

**Pedido do usuário:** floating text (números de dano) ficou bagunçado
aparecendo em cima da HUD nova — pediu pra nascer da BASE do personagem
em vez de acima da cabeça, "na frente do personagem", mesma animação.

**Fix:** `ui/floating_text.py::FloatingTextManager.BASE_Y_OFFSET` mudou de
`20` (positivo — `wy - 20`, 20px ACIMA do centro) pra `-14` (negativo —
`wy - (-14) = wy + 14`, 14px ABAIXO do centro, perto da base/pés).
Animação inalterada (deriva pra cima + empilhamento por alvo) — só o
ponto de partida mudou, então o texto agora sobe A PARTIR da base,
passando na frente do sprite (a camada de floating text já desenha por
cima da entidade).

Validado: `py_compile`; suíte sem regressão; teste end-to-end (mesmo
screenshot da 23.9) confirma o número nascendo abaixo do sprite.

---

### 23.11 Fonte do número de nível ilegível (12px sumindo dentro da caixa 3x maior) (11/07/2026)

**Reportado pelo usuário (com screenshot):** depois da 23.9, o número do
nível ficou minúsculo dentro da caixinha — apontou (com razão) que eu não
tinha conferido visualmente o resultado antes de reportar como pronto.
Causa: a caixinha cresceu 3x (`SCALE`, pra caber o "99" sem vazar — 23.9),
mas a fonte do número continuou em 12px (escolha antiga, de quando a
caixa era nativa) — sobrou caixa vazia enorme ao redor de um número
minúsculo.

**Fix — testado visualmente, não calculado:** gerei uma comparação lado a
lado com 12/16/20/24/28/32px (`ui/hud_bars.py::LEVEL_FONT_SIZE`, nova
constante) e OLHEI o resultado antes de decidir — 24 preenche bem os dois
tamanhos de caixa (player quadrado 42px, mob círculo 36px) sem vazar; 28+
já estoura o círculo do mob (mob é a restrição mais apertada, círculo
inscrito tem menos área útil que o quadrado do player). `LEVEL_FONT_SIZE`
centralizado em `ui/hud_bars.py`, usado nos 3 call sites (player local em
`ui/systems.py`, player e mob remotos em
`client/remote_entity_handlers.py`) — um valor só, fácil de reajustar se
o usuário pedir de novo.

Validado: `py_compile`; suíte sem regressão (7F/85P); **conferido
visualmente antes de reportar** (screenshot da comparação 12-32px +
re-render do teste end-to-end da 23.9 com o valor final) — "99" legível,
centralizado, preenchendo a caixa sem vazar, nos dois tipos de HUD.

---

### 23.12 Reversão: HUD ficou grande demais mesmo com a fonte corrigida — SCALE 3→2 (11/07/2026)

**Reportado pelo usuário:** a 23.11 corrigiu a fonte, mas não reduziu o
tamanho da CAIXA — o usuário queria a HUD de volta a um tamanho parecido
com o de antes da 23.9, só com fonte legível dentro dela (sugeriu testar
fonte 14).

**Fix — testado visualmente ANTES de aplicar (aprendizado da 23.11):**
gerada comparação lado a lado de `(SCALE, fonte)` = `(1,10) (1,14) (2,14)
(2,16) (3,14)` — `SCALE=1` (nativo) não cabe nem fonte pequena sem vazar
(caixa native é menor que qualquer fonte legível); `SCALE=2` com fonte 14
cabe "99" sem vazar E é visivelmente mais compacto que `SCALE=3`.
Aplicado: `ui/hud_bars.py::SCALE = 2`, `LEVEL_FONT_SIZE = 14`.

Validado: `py_compile`; suíte sem regressão (7F/85P); re-render do mesmo
teste end-to-end de ponta a ponta (player + 3 bonecos + efeito + floating
text) conferido visualmente — HUD mais compacta, "99" ainda legível sem
vazar, fila de efeitos e floating text continuam alinhados (dependem do
tamanho da Surface da HUD dinamicamente, não precisaram de ajuste).

---

### 23.13 Fonte 14→16 + causa raiz do "não parece centralizado": centralizar pela tinta, não pelo tamanho nominal da fonte (11/07/2026)

**Reportado pelo usuário (com screenshot):** ainda sobrava espaço pra
fonte maior (pediu 16), e o número não parecia centralizado no quadrado.

**Causa raiz do desalinhamento (medida, não suposta):**
`font.render("99", ...)` devolve uma Surface do tamanho da LINHA da fonte
inteira (inclui espaço reservado pra acento/descendente, ex: a "cauda" de
um "g" ou "y") — mas dígitos como "9" não usam esse espaço. Medido
diretamente: `"99"` em 16px gera uma Surface de 13px de altura, mas a
tinta visível ocupa só 7px, começando em `y=4` (não `y=0`). Centralizar
pela Surface inteira (`surf.width/2`, `surf.height/2`, o que já estava
sendo feito) deslocava o número visualmente pra cima do centro real da
caixa, porque metade da Surface é espaço vazio que o "9" nunca usa.

**Fix:** `ui/hud_bars.py::_blit_centered_by_ink()` (novo) — usa
`pygame.mask.from_surface(...).get_bounding_rects()` pra achar o
retângulo REAL da tinta (não o nominal da fonte) e centraliza por esse
retângulo. `LEVEL_FONT_SIZE` subiu de 14 pra 16 (ainda cabe sem vazar,
usa melhor o espaço — 18 já toca a borda do círculo do mob).

Validado: `py_compile`; suíte sem regressão (7F/85P); **diagnóstico visual
com cruz marcando o centro geométrico da caixa** sobreposta ao número
renderizado (12/14/16/18px) — antes do fix a cruz caía visivelmente acima
do centro do "99"; depois do fix, a cruz cai exatamente no meio do
glifo, nos dois tipos de HUD, em todos os tamanhos testados; re-render do
teste end-to-end completo confirma o resultado final.

---

### 23.14 Barra de recurso tampava o contorno inferior + vão gigante até a fila de efeitos (11/07/2026)

**Reportado pelo usuário (com screenshot):** a barra de raiva/mana/
concentração cobria a borda preta de baixo da HUD, e o ícone de efeito
aparecia bem longe da HUD em vez de colado nela.

**Causa raiz #1 (barra tampando borda):** `P_RES_Y = (10, 12)` incluía a
linha 12 do asset — mas o mapeamento pixel a pixel (RLE, já feito antes)
mostra que a linha 12 é a BORDA PRETA inferior (preto sólido nas 48
colunas da barra), não faz parte do preenchimento — só as linhas 10-11
são a barra de verdade. Preencher a linha 12 com a cor do recurso pintava
por cima do contorno. Fix: `P_RES_Y = (10, 11)`.

**Causa raiz #2 (vão gigante):** `effects_row_offset()` calculava
`xo = hud_surf.get_width() + EFFECTS_GAP_PX` — mas a âncora usada
(`position.x`) é o CENTRO da HUD, não a borda esquerda. Pra chegar na
borda direita a partir do centro só precisa de METADE da largura, não da
largura inteira — sobrava um vão do tamanho da HUD inteira entre ela e a
fila de efeitos. Fix: `xo = hud_surf.get_width() / 2 + EFFECTS_GAP_PX`.

Validado: `py_compile`; suíte sem regressão (7F/85P); render isolado da
HUD do player em zoom 6x confirma o contorno preto de baixo intacto (não
mais coberto pela barra de raiva); re-render do teste end-to-end completo
confirma o ícone de efeito colado na borda direita da HUD.

---

### 23.15 Ícones do mapa/minimapa (treinador etc.) 8px→16px (11/07/2026)

**Reportado pelo usuário (com screenshot):** os ícones de treinador
(agora com arquivos reais em `assets/icons/`, ex. `map_trainer_guerreiro.png`)
apareciam minúsculos/irreconhecíveis no mapa e minimapa. Pedido: 2x.

**Contexto:** o tamanho de 8px (nativo, sem upscale) foi pedido
explicitamente pelo usuário na Decisão 23.1/23.2 pra evitar "esticar"
demais um ícone pequeno — mas com os arquivos reais em mãos (na época só
existia `map_death.png`), 8px se mostrou pequeno demais pra reconhecer o
desenho de verdade em jogo.

**Fix:** nova constante `ui/map_markers.py::MAP_ICON_SIZE = 16` (fonte
única, substitui os dois `icon_size = 8` duplicados em
`ui/map_overlay.py`/`ui/minimap.py`) — continua fixo (não escala com zoom
do mapa nem com `tp` do minimapa, mesma regra de sempre), só o valor
mudou. Ainda nearest-neighbor a partir do arquivo-fonte 8×8 — 16 é
exatamente 2x, upscale limpo sem esticar de forma desproporcional.

Validado: `py_compile`; suíte sem regressão (7F/85P); render dos 5 ícones
reais (`map_trainer_guerreiro/mago/arqueiro`, `map_merchant`, `map_death`)
no tamanho novo, conferido visualmente — nítidos e reconhecíveis (espada,
cajado, arco, bolsa, ícone de morte).

---

### 23.16 Vão grande antes do "i" nos nomes — bearing desproporcional da MEGAMAN10 (11/07/2026)

**Reportado pelo usuário (com screenshot):** "Zumbi" aparecia como
"Zumb i", "Custodio Benevide" como "Custod io Benev ide" — vão visível
antes de todo "i".

**Causa raiz (medida via `font.metrics()`, não suposta):** o glifo "i" da
MEGAMAN10 tem `advance=6px`, mas a tinta só começa em `x=3` dentro dessa
célula — ou seja, quase METADE do avanço do caractere é espaço vazio
reservado antes do desenho. `font.render()` respeita esse bearing
literalmente, produzindo o vão. Não é bug de código, é como o arquivo
`.ttf` foi desenhado — mas dava pra corrigir sem trocar de fonte.

**Fix:** `ui/fonts.py::render_tight()` (novo) — renderiza caractere por
caractere e reempacota pela TINTA REAL de cada um (`pygame.mask`, mesma
técnica da 23.13) + respiro fixo de 1px, descartando o bearing/kerning
original da fonte. Pra uma fonte pixel (quase monoespaçada por natureza),
isso não perde nada perceptível. Cacheado por `(fonte, texto, cor, gap)`
— texto de nome não muda todo frame. `ui/world_labels.py::add_text()`
trocou `font.render()` por esse helper — corrige TODO nome (NPC/mob/
player) de uma vez, um lugar só.

Validado: `py_compile`; suíte sem regressão (7F/85P); comparação lado a
lado render normal vs. `render_tight` pra "Zumbi"/"Custodio Benevide" —
vão desaparece; re-teste isolado pela pipeline real (`RenderSystem` +
`WORLD_LABELS`) confirma "Custodio Benevide" renderizando limpo em jogo.

---

### 24. Aljava sempre voltava cheia no relogin — save de equipamento usava cache stale do cliente (11/07/2026)

**Reportado pelo usuário:** deslogou o arqueiro com 4 flechas na aljava,
relogou e a aljava estava cheia (75/75).

**Investigação** (agente `Explore` em paralelo + verificação direta):
achou DOIS bugs empilhados, ambos no lado do servidor.

**Bug 1 — `WorldServer._item_data_from_obj`** (`server/world_server.py`):
o dict que serializa um item do ECS pra cache de save (usado por
`get_player_equipment_data`, chamado depois de todo `EQUIP_SYNC`) nunca
incluía `arrow_count`/`max_arrows` — faltava o bloco `if item_type ==
"quiver"` que a versão do CLIENTE (`_serialize_item`) e a versão antiga
de save single-player (`engine/save_system.py::_item_to_dict`) já tinham.
Mesmo se o resto do fluxo estivesse certo, esse dict sempre "esquecia"
quantas flechas tinham.

**Bug 2 (a causa raiz de verdade) — `SessionManager._build_save_merge`**
(`server/session.py`): por convenção documentada ("autoridade por
campo"), `equipment` é tratado como client-autoritativo no merge —
`client_p.get("equipment")`, onde `client_p` é `session.last_client_payload`,
um CACHE do último `EQUIP_SYNC`/`SAVE_STATE` que o cliente mandou. Esse
cache só é atualizado em 2 situações: equipar/desequipar um item, ou
Recarregar (que dispara `_send_save_state()` explicitamente). **Consumir
flecha em combate normal (auto-attack) é 100% server-side** —
`_server_apply_ranged_physical` mexe direto no `Equipment.arrow_count` do
ECS sem nunca avisar o cliente — então o cache nunca era atualizado
depois disso. No disconnect/autosave, esse cache STALE (contagem de
quando a aljava foi equipada — cheia) sobrescrevia o banco, apagando o
consumo real.

**Fix:**
- `_item_data_from_obj`: adiciona `arrow_count`/`max_arrows` pra itens
  `quiver` (mesmo padrão do client `_serialize_item`).
- `_build_save_merge` ganhou um 3º parâmetro `live_equipment` — quando
  fornecido, usa ele em vez do cache do cliente pro campo `equipment`.
  Todo os 6 call sites (`on_disconnect`, `_handle_save_state`,
  `_handle_talent_update`-like, `_autosave_all`, etc.) agora chamam
  `WorldServer.get_player_equipment_data(session_id)` (já existia, usado
  por `EQUIP_SYNC`, só nunca tinha sido reaproveitado nos pontos de save)
  ANTES de montar o merge — Equipment ATUAL do ECS, nunca desatualizado.

Validado: `py_compile`; suíte sem regressão (7F/85P); **teste headless
reproduzindo o cenário exato**: aljava equipada 75/75, consumida até 4
via mutação direta do ECS (mesma coisa que `_server_apply_ranged_physical`
faz), SEM nenhum EQUIP_SYNC novo — `get_player_equipment_data` confirma
`arrow_count=4` capturado corretamente, e `_build_save_merge` com esse
valor e um cache de cliente stale (75) produz `arrow_count=4` no dict
final que iria pro banco.

**Não validado:** sessão manual completa (equipar aljava → atirar em
combate real → deslogar → relogar) confirmando a contagem certa vindo do
banco de verdade (o teste validou a lógica de merge isoladamente, não o
fluxo de rede+DB ponta a ponta).

---

### 25. Ícones de mapa/minimapa não exigem mais linha de visão (11/07/2026)

**Pedido do usuário:** os ícones de mapa/minimapa (quest giver, treinador,
mercador) só apareciam quando o NPC estava dentro do componente `Visible`
(tag dinâmica de FoW/linha de visão) — mas esses ícones servem pra GUIAR
o player, diferente do indicador acima da cabeça no MUNDO (que faz
sentido exigir visão direta, já que é desenhado em cima do NPC de
verdade na tela). Um quest giver do outro lado de uma parede, ou fora do
raio de visão atual mas na mesma zona carregada, deveria continuar
aparecendo no mapa/minimapa — é exatamente quando o jogador mais precisa
do ícone pra se guiar até lá.

**Fix:** `ui/map_markers.py::collect_markers` — removido o componente
`Visible` da query de `QuestGiver`/`Trainer`/`Merchant`
(`world.get_entities_with(TileMovement, QuestGiver)` em vez de
`(TileMovement, QuestGiver, Visible)`, idem pros outros dois). Morte
(`GhostState`) já não dependia disso. O indicador acima da cabeça no
MUNDO (`QuestDialogSystem.render_world`, `TrainerSystem.render_world`)
continua exigindo `Visible` — não mudou, faz sentido diferente do
mapa/minimapa.

Efeito colateral positivo: resolve de graça a limitação já documentada na
Decisão 23 (mapa grande não mostrava NPC fora do campo de visão atual,
mesmo em área já explorada) — não precisou de nenhum conceito novo de
"NPC conhecido", só parar de filtrar por `Visible`.

Validado: `py_compile`; suíte sem regressão (7F/85P); teste headless — um
treinador SEM componente `Visible` (fora de FoW) e um mercador COM
`Visible` — confirma que os dois aparecem na lista de `collect_markers`
(antes do fix, o treinador sem `Visible` seria descartado).

---

### 26. Aljava do arqueiro dessincroniza ao usar skill de flecha — auto-attack trava "sem munição" mas sem aviso (13/07/2026)

**Reportado pelo usuário:** "tem 1 flecha na aljava do arqueiro mas ele não
consegue atacar, não aparece a mensagem de que a aljava está vazia, mas
também não sai o ataque."

**Causa raiz:** `_server_apply_ranged_physical` (`spell_completion_processor.py`,
única função que desconta flecha de verdade) é chamada por 5 caminhos: o
auto-attack (`combat_processor.py`) e as 4 skills de flecha (Picada de
Escorpião, Flecha Reiterada, Tiro Repulsivo, Tiro Múltiplo). Só o
auto-attack tinha o espelho client-side (`client/remote_entity_handlers.py:263`,
decrementa a cópia LOCAL da aljava ao receber `COMBAT_RESULT` — ver
Decisão 21). As 4 skills descontavam a flecha SÓ no servidor — nenhum
`STATS_UPDATE`/confirmação avisava o cliente, então `Equipment.offhand
.arrow_count` local nunca refletia esses usos, ficando cada vez mais
ACIMA do valor real do servidor a cada skill de flecha usada. Com o
cliente "achando" que ainda tem munição (não dispara o aviso "Aljava
vazia! Use Recarregar." de `ui/systems.py::_process_archer_combat`, que
só olha a cópia local), o auto-attack seguinte chegava ao servidor, que
recusava silenciosamente por munição real esgotada (`combat_processor.py`
linha ~114, um `continue` sem nenhum feedback ao cliente — mesmo
tratamento silencioso de LOS/alcance/cooldown) — o tiro simplesmente não
saía, sem nenhuma mensagem.

**Fix:** `_server_apply_ranged_physical` agora enfileira
`queue_stats_update({"player_eid": player_eid, "quiver_arrow_count":
_qv_ar.arrow_count})` logo após descontar a flecha (mesmo padrão já usado
por Recarregar) — cobre os 5 chamadores de uma vez só (ponto único de
verdade, nenhuma skill precisou de código próprio). Cliente já tinha o
handler genérico pronto (`client/network_handlers.py::_handle_msg_stats_update`,
`if "quiver_arrow_count" in payload:`), sem alteração necessária ali.

Validado: `py_compile`; suíte sem regressão (7F/85P).

**Não validado:** sessão manual (usar Picada de Escorpião/Flecha
Reiterada/Tiro Repulsivo/Tiro Múltiplo algumas vezes e confirmar que o
HUD da aljava cai em tempo real, e que o auto-attack acusa "Aljava
vazia!" corretamente quando a munição de verdade acaba).

---

### 27. Modais de diálogo de quest e de atalhos do teclado transbordavam a tela — sem clip/scroll (13/07/2026)

**Reportado pelo usuário:** prints mostrando (1) o modal "Atalhos do
teclado" (tecla K) com as linhas de baixo (barra de consumíveis) cortadas
no fundo da tela e os botões Salvar/Fechar soltos no meio do conteúdo, e
(2) o diálogo de quest do NPC ("Prova de Valor") com a descrição vazando
pra baixo do painel, sobrepondo os botões Aceitar/Recusar. Pedido:
conteúdo deve ficar CONTIDO dentro da borda do modal, com barra de
rolagem quando não couber; e todo modal do jogo com barra de rolagem deve
também rolar com o scroll do mouse.

**Causa raiz:** os dois modais desenhavam o conteúdo com um `cy`/`y`
incremental sem nenhum teto — a altura do painel (`PH`) ou crescia pra
acomodar TUDO (`hotbar_editor_handlers.py`, `base_PH` calculado a partir
de `n_rows`, sem limite) ou era fixa mas o conteúdo (descrição de quest,
tamanho variável por definição) não respeitava esse limite
(`quest_system.py::_render_detail`/`_render_turnin`, botões desenhados
numa posição FIXA no fim do painel, texto acima sem clip nenhum).
Nenhum dos dois tinha estado de scroll.

**Fix — mesmo padrão nos dois lugares:** altura do painel vira um teto
fixo (`UI.HOTBAR_EDITOR_MAX_H` novo; `QUEST_DIALOG_H` já era fixo) e a
área de conteúdo variável vira uma viewport com `set_clip()` +
scroll em px, com barra de rolagem (thumb proporcional, mesmo visual já
usado por `crafting_system.py`) quando o conteúdo não cabe:
- `ui/quest_system.py::_blit_scrollable()` — novo helper: recebe uma
  lista de `(surf, rel_y)` (coordenadas relativas ao topo do bloco),
  mede a altura total, clampa `self._detail_scroll` (px) a
  `[0, content_h - view_h]`, desenha com `set_clip()` na faixa entre o
  header e os botões (que continuam FIXOS, fora do clip). Usado por
  `_render_detail` e `_render_turnin`. Scroll reseta a 0 toda vez que o
  diálogo entra em "detail"/"turnin" (`_open_dialog`, clique na lista).
- `client/hotbar_editor_handlers.py::_draw_hotbar_editor` — mesma ideia,
  porém as linhas (`draw_row`) são interativas (hover/clique pra
  rebind): uma linha rolada pra fora da viewport (`visible`/
  `row_fully_visible`) não recebe hover nem clique, senão um clique
  "invisível" atrás do clip ainda acionava rebind da linha errada.
- Mouse wheel: `quest_system.py::handle_events` ganhou um branch
  `MOUSEWHEEL` (`self._detail_scroll -= event.y * self._u(24)`, clampado
  no próprio `_blit_scrollable` no próximo frame); `_draw_hotbar_editor`
  ganhou o mesmo, lendo os `events` que já recebe direto (não passa por
  `handle_events`, é chamado 1x por frame em `game.py`).
- `game.py::_handle_scroll_zoom` (zoom da câmera com scroll do mouse) já
  tinha uma lista de "modal aberto bloqueia zoom" que incluía
  `_show_hotbar_editor` mas NÃO o diálogo de quest — sem isso, rolar o
  nosso scroll novo também zoomaria a câmera ao mesmo tempo. Adicionado
  `self._quest_dialog.is_open` à lista.

Demais modais com barra de rolagem já tratavam `MOUSEWHEEL`
individualmente (`chat_handlers.py`, `crafting_system.py`,
`trainer_system.py`, `habilidades_handlers.py`, `map_overlay.py`,
`god_mode.py`, diário de quests em `quest_system.py`, debug F12 via
`game.py`) — não precisaram de mudança.

Validado: `py_compile` nos arquivos tocados; suíte sem regressão
(7F/85P); renderização headless (SDL dummy driver) dos dois modais com
conteúdo propositalmente maior que a viewport — confirma clip+scrollbar
funcionando (screenshot conferido visualmente antes de reportar) e o
scroll de mouse simulado via evento `MOUSEWHEEL` real (com
`pygame.display.set_mode`, necessário pro rastreio de mouse funcionar
headless) alterando `self._mkb_scroll` corretamente (clampado nos dois
extremos).

**Não validado:** sessão manual em jogo real (redimensionar/ter muitos
slots de hotbar e rolar com o mouse de verdade; abrir uma quest com
descrição longa e conferir a barra de rolagem+scroll do mouse).

---

### 28. "Só um Gole" (arqueiro): Concentração grátis expira ~2x mais rápido que os 10s prometidos — buff falha "às vezes" (14/07/2026)

**Reportado pelo usuário:** usar "Último Gole" (Só um Gole) às vezes não
deixa as skills de Concentração grátis como a descrição promete
("habilidades de Concentração ficam grátis... por 10s") — o custo é
cobrado mesmo com o buff supostamente ainda ativo.

**Causa raiz:** `server/world_server.py` tinha um bloco manual ("Arqueiro:
regen de Concentração + timers de buff") que DUPLICAVA por completo o que
`core_systems.ServerCombatStateSystem.update()` já faz — e que já é
chamado nesta mesma função, mais acima (`self._combat_state_sys.update(...)`,
que internamente roda `_tick_concentration_regen` e
`_tick_concentration_free_timer` pra cada player). Os dois blocos rodavam
no MESMO tick, sem nenhuma guarda contra duplicação:
- `concentration_free_timer` (contagem regressiva do buff de "Só um
  Gole") decrementava `dt` DUAS vezes por tick → os 10s de duração
  configurados em `skill_config.py` na prática expiravam em ~5s reais no
  servidor (autoritativo — quem decide o desconto de Concentração em
  `spell_completion_processor.py::_process_spell_cast_completions`).
  Nada avisa o cliente dessa expiração antecipada (não existe
  resincronização de `concentration_free` via STATS_UPDATE), então o
  jogador via o buff "ainda dentro dos 10s" (contagem local, client-side,
  ticando na velocidade CORRETA de 1x) mas o servidor já tinha voltado a
  cobrar o custo havia segundos — exatamente o "às vezes" relatado
  (dependia de quanto tempo se passava entre ativar o buff e usar a
  próxima skill custosa).
- Regen de Concentração (fora de combate) também dobrava de velocidade
  pelo mesmo motivo (mesma fórmula, mesmo `dt`, calculada duas vezes) —
  efeito colateral não reportado mas real, corrigido junto.
- `camouflage_timer` (Camuflagem) só existia nesse bloco manual — não
  duplicado em `ServerCombatStateSystem`, preservado como estava.

**Fix:** removido o bloco manual de regen de Concentração +
`concentration_free_timer` de `world_server.py` — `ServerCombatStateSystem`
(já chamado antes, mesma função) passa a ser a ÚNICA fonte de verdade
pros dois, igual ao cliente (`engine/world_systems.py::PlayerCombatStateSystem`,
que sempre teve só UMA chamada de cada). O bloco que sobrou trata só
`camouflage_timer`, sem mexer em Concentração.

Validado: `py_compile`; suíte sem regressão (7F/85P); inspeção de código
confirmando 1 única chamada de `_tick_concentration_free_timer`/
`_tick_concentration_regen` em cada lado (cliente e servidor) após o fix
(antes: 2 no servidor, 1 no cliente).

**Não validado:** sessão manual em jogo real (usar "Só um Gole", esperar
~6-9s e confirmar que uma skill de Concentração ainda sai grátis dentro
da janela de 10s; conferir também que a regen de Concentração fora de
combate não ficou mais lenta do que antes — o valor "correto" agora é
metade da velocidade observada antes do fix, que estava dobrada por
engano).

---

### 29. Investigação de performance com o arqueiro (14/07/2026) — 2 achados

**Reportado pelo usuário:** "ainda tem algo com o desempenho" ao andar
com o arqueiro; depois, com print do overlay de F11: um texto de 2
dígitos ilegível, sobreposto às linhas do profiler, sempre no canto
superior esquerdo.

**Achado 1 — `debug/archer_debug.py` com log ligado:** `DBG_ENABLED`
estava `True` (deixado ligado da investigação do bug de auto-attack
desta sessão) — client e servidor gravavam uma linha por decisão de
disparo (ATTEMPT/BLOCK/LOS/FIRE/ARROW/AGGRO) em
`logs/archer_debug_{client,server}.log`. Fix: `DBG_ENABLED = False`.
Outros flags de debug do projeto (`aoi_debug.py`, `mob_combat_debug.py`,
`spell_debug_log.py`) já estavam desligados — conferido, não precisou
mexer.

**Achado 2 (real, mas modesto) — `ui/hud_bars.py` sem cache do número de
nível:** `build_player_hud`/`build_mob_hud` (HUD de nível+barras acima da
cabeça, feature desta sessão) recalculavam `pygame.mask.from_surface(...)
.get_bounding_rects()` do número do nível TODO FRAME, pra CADA entidade
visível com barra de HP — custo que escala com quantos mobs estão em
campo de visão (cresce ao andar por áreas mais povoadas). Fix: número do
nível (`surf`, centro de tinta) cacheado por `(level, id(font))` — level
muda raríssimo (level up), cache quase sempre quente. Nova função
`_level_surf()` substitui `_blit_centered_by_ink()` (removida, sem outros
call sites). Microbenchmark: ~15.1µs → ~9.8µs por chamada (~35%) — real,
mas não explica sozinho uma queda perceptível de FPS.

**Achado 3 (a causa real do "texto de 2 dígitos"):** não era um bug de
performance — era sobreposição visual de DOIS painéis independentes, os
dois ancorados no canto superior esquerdo:
- `_draw_perf_overlay()` (`game.py`, o próprio overlay de F11): `self.
  screen.blit(surf, (4, 4))`.
- `_draw_hud()` (`client/hud_handlers.py`, HUD de texto permanente:
  nome/HP/recurso da classe/aljava) desenha a partir de
  `(self._u(10), self._u(10))` pra baixo — pro arqueiro, a linha
  "Conc. XX/XX  (+N/s)" (`hud_handlers.py:92`, `_rate_str = f"  (+{_rate:
  .0f}/s)"`) cai bem na faixa vertical onde o overlay de F11 também
  desenha suas primeiras linhas. `_rate` só é > 0 (e portanto o texto só
  aparece) quando há regen de Concentração ativo — daí "sempre que o
  arqueiro anda" (regen idle/moving são taxas diferentes, mas quase
  sempre > 0 fora de combate) e nunca é notado sem F11 aberto (os dois
  painéis convivem bem SEM o overlay de debug por cima). O "2 dígitos"
  era literalmente o "+6" de "(+6/s)" ilegível por causa da sobreposição.

**Fix:** `_draw_perf_overlay()` (`game.py`) desenha em `(4, 110)` em vez
de `(4, 4)` — 110px é espaço suficiente pro HUD de texto (nome+HP+recurso
+aljava, ~90px de altura) nunca encostar no overlay de debug. Overlay de
F11 é dev-only; mover ele (não o HUD de gameplay) é a escolha certa.

Validado: `py_compile`; suíte sem regressão (7F/85P).

**Não validado:** sessão manual em jogo real confirmando visualmente que
os dois painéis não sobrepõem mais com F11 aberto, e que o "desempenho"
percebido melhora minimamente com o Achado 2 (não esperado resolver
sozinho uma queda de FPS grande — se persistir, precisa de outro print
de F11 focado só nas seções mais altas, sem o "2 dígitos" ilegível
atrapalhando a leitura).

**Adendo (mesmo dia):** usuário esclareceu — a dúvida real não era o
texto sobreposto (Achado 3), era descobrir o que CAUSA um spike de frame
ao andar com o arqueiro. Como o spike "acontece em uma fração de
milésimo" (rápido demais pra print manual), pediu log automático em vez
de captura manual.

Infra já existia (`game.py::run()`, perto do fim do loop) — detecção de
spike de frame com breakdown por seção gravado em `logs/client_prof.log`
(criado do zero a cada execução), ativo sempre que `PROFILE_FRAMES=True`
(setado ao apertar F11 uma vez — fica ligado o resto da sessão mesmo
fechando o overlay visual depois). Dois problemas nela pro caso de uso
atual: **(1)** threshold de 50ms só pegava travadas grandes — o budget é
16.7ms a 60 FPS, um engasgo de 20-35ms (perceptível, mas bem menor que
50ms) passava batido. **(2)** a linha `[SPIKE]` só tinha o breakdown por
seção, sem nenhum dado do PLAYER — não dava pra confirmar de cara se o
spike coincidia com "andando/perseguindo como arqueiro" sem cruzar
timestamp a mão contra outro log.

**Fix:** `game.py::run()` — `_SPIKE_THRESHOLD_S` (novo, `__init__`) reduz
o gatilho de 50ms pra 22ms; a linha `[SPIKE]` agora inclui
`class_id`/`is_moving`/`is_pursuing`/`attack_cooldown_timer` do player
local (lidos com fallback seguro — `try/except` — pra nunca quebrar o
loop principal por causa de instrumentação). Nenhuma mudança de
comportamento fora do bloco `if PROFILE_FRAMES:` (custo zero quando o
profiler está desligado, igual antes).

Validado: `py_compile`; suíte sem regressão (7F/85P).

**Adendo 2 (mesmo dia) — CAUSA REAL encontrada via `logs/client_prof.log`:**
usuário reproduziu e o log capturou dezenas de `[SPIKE]` (22-70ms,
threshold reduzido do Achado acima). Analisando os `[PROF]` agregados de
várias janelas de 300 frames, UMA seção domina de forma consistente:
`hud:combat_log` — nome ENGANOSO no profiler (`game.py`), na real
cronometra o bloco `self._minimap.render(...)` +
`self._quest_system.render_hud(...)`, não o log de combate. Avg 0.5-0.9ms
mas PEAK de 12-19.6ms, aparecendo em praticamente toda janela — bem mais
consistente que qualquer outra seção.

Causa raiz: `ui/minimap.py::Minimap._rebuild_numpy()` — o cache do
minimapa (`cache_key = (player_tx, player_ty, len(explored))`, linha
~108) invalida toda vez que o player muda de TILE — ou seja, toda vez que
anda. No rebuild, `explored` (o `set` de `FogOfWar`, que só CRESCE e
nunca encolhe — "persiste entre movimentos", `engine/components.py`) era
convertido INTEIRO pra numpy (`np.array(list(explored))`) antes de
filtrar só os tiles que cabem na janela de 51×51 (`RADIUS=25`) do
minimapa — processava o histórico de exploração da sessão inteira (que só
cresce, podendo chegar a milhares de tiles num mapa grande já explorado)
pra descartar quase tudo logo em seguida. Custo crescendo sem limite
conforme mais mapa é revelado — explica tanto "sempre que anda" (rebuild
só dispara em troca de tile) quanto o padrão de piorar ao longo da
sessão.

**Fix:** `_rebuild_numpy()` agora intersecta `explored`/`visible` com um
`set` da JANELA atual (bounded, `win²` ~2601 tiles, construído a cada
rebuild mas barato) ANTES de converter pra numpy — `set & set` em CPython
sempre itera o MENOR dos dois operandos, então com `window_tiles` bounded
o custo vira O(win²) CONSTANTE, nunca mais O(len(explored)). `visible`
recebeu o mesmo tratamento por consistência (já era pequeno — raio de LOS
~8 tiles — mas mantém o mesmo padrão). `_rebuild_python` (fallback sem
numpy) não precisou de mudança — já fazia checagem de pertencimento
tile-a-tile dentro da janela (`(tx,ty) not in explored`), sempre O(win²).

Validado: `py_compile`; suíte sem regressão (7F/85P); teste de
corretude headless (mesmo `explored`/`visible` sintéticos, incluindo
tiles negativos/fora da janela — resultado do fog ANTES vs DEPOIS
`np.array_equal` idêntico); microbenchmark com 8000 tiles explorados
(~sessão longa): 2205µs → 746µs por rebuild (~3x mais rápido) — e a
vantagem CRESCE ainda mais quanto mais o mapa for explorado, já que a
versão antiga era O(n) e a nova é O(1) em relação a `len(explored)`.

**Não validado:** sessão manual em jogo real confirmando que o engasgo
some/reduz ao andar com o arqueiro (esperado, já que a causa raiz mais
provável — confirmada pelos próprios logs do usuário — está corrigida),
e que o minimapa continua visualmente correto (fog/exploração) em jogo
de verdade, não só no teste sintético.

**Adendo 3 (mesmo dia) — `display_flip` engasgando mesmo parado, sem
correlação com ação nenhuma:** usuário testou de novo depois do fix do
minimapa e reportou que a queda de FPS também acontece parado, sem
nenhuma ação. Log já tinha crescido pra 2735 linhas — analisado de novo.

Achado: em praticamente TODO `[SPIKE]` do log (a esmagadora maioria com
`is_moving=False is_pursuing=False`), a seção `display_flip`
(`pygame.display.flip()`, presenteção do frame) domina — 5-47ms,
totalmente errático, sem relação com nenhum sistema do jogo específico
(às vezes sozinha, às vezes com `transform_scale` — que também teve um
pico isolado de 26ms). Isso é a assinatura clássica de jitter de VSync em
janela no Windows (compositor DWM segurando o present).

Causa provável: `game.py` configurava `pygame.display.set_mode(...,
vsync=1)` **E** já fazia pacing manual de FPS via
`self.clock.tick_busy_loop(FPS)` no loop principal (`run()`) — dois
mecanismos de controle de frame rodando ao mesmo tempo, brigando entre
si. `vsync=1` em janela (não fullscreen exclusivo) no Windows é conhecido
por causar exatamente esse tipo de stall imprevisível independente da
carga real de trabalho.

**Fix (aprovado pelo usuário via pergunta explícita — trade-off aceito):**
`vsync=0` nos dois `pygame.display.set_mode()` (`__init__` e
`_apply_scale()`, resolução muda no menu de opções) — o `tick_busy_loop`
já existente assume 100% do pacing de FPS. Risco aceito: pode aparecer
tearing (corte horizontal) se o FPS variar — não mitigado agora (usuário
optou por testar vsync desligado antes de considerar tornar
configurável).

Validado: `py_compile`; suíte sem regressão (7F/85P) — testes são
server-side headless, não tocam nesse código.

**Não validado:** sessão manual em jogo real confirmando que os spikes de
`display_flip` desaparecem/reduzem, e que não há tearing perceptível. Se
o tearing incomodar, próximo passo natural é a opção "Deixa configurável
nas opções" que o usuário não escolheu desta vez (toggle de VSync no menu
de opções).

**Adendo 4 (mesmo dia) — `vsync=0` resolveu o jitter, mas trouxe tearing
de volta:** usuário confirmou FPS estável com `vsync=0`, mas reportou
tearing perceptível depois de eu explicar o que é. Perguntou como
engines grandes resolvem — expliquei (swap chain com apresentação por
hardware/"flip model", fullscreen exclusivo, VRR) e propus testar trocar
pro caminho acelerado do SDL (`pygame.SCALED`) com vsync ligado de novo,
em vez de aceitar o tearing permanentemente ou só desligar vsync.
Usuário aprovou o teste.

**Fix:** `game.py` — os dois `pygame.display.set_mode(...)` (`__init__`
e `_apply_scale()`) voltam a `vsync=1`, mas agora com a flag
`pygame.SCALED` adicionada (`pygame.DOUBLEBUF | pygame.SCALED`). Sem
`SCALED`, `set_mode()` cria uma surface de software (caminho GDI/blit por
CPU) — é nesse caminho que `vsync=1` em janela no Windows trava o
`flip()` esperando o compositor (DWM) de forma inconsistente (Adendo 3).
`SCALED` troca pro `SDL_Renderer` acelerado por hardware, que no Windows
usa apresentação em "flip model" (DXGI) — o mesmo princípio de swap chain
que engines grandes usam pra ter vsync sem tearing E sem o overhead do
compositor. Como `win_w`/`win_h` passados pro `set_mode()` já são
EXATAMENTE o tamanho da janela (a lógica de escala do jogo já embute o
fator em `win_w = int(1280*scale)` antes de chamar `set_mode`), `SCALED`
não faz nenhum upscale de verdade aqui — só troca o caminho de
apresentação, sem mudar a nitidez/resolução renderizada.
`clock.tick_busy_loop(FPS)` continua fazendo o pacing de FPS como antes
— vsync agora serve só pra eliminar tearing, sem competir pelo controle
do frame rate.

Validado: `py_compile`; suíte sem regressão (7F/85P, headless, não toca
nesse código); smoke test estrutural (SDL dummy driver) confirmando que
`set_mode(SCALED|DOUBLEBUF, vsync=1)` + blit + `flip()` não lança exceção,
inclusive numa troca de resolução simulando `_apply_scale()` (960×540 em
cima de uma janela já aberta em 1280×720) — mas o dummy driver não tem
GPU de verdade, então NÃO valida vsync/tearing/aceleração de hardware de
verdade, só a ausência de crash.

**Não validado (importante — só dá pra confirmar na máquina do
usuário):** se o `SDL_Renderer` acelerado realmente engata no driver de
vídeo do usuário (sem GPU compatível, pode cair num fallback por
software que reintroduz o jitter do Adendo 3); se o tearing some de
verdade com vsync+SCALED; se a nitidez/escala da imagem permanece
idêntica a antes (não deveria mudar, já que a escala lógica sempre bate
1:1 com o tamanho da janela, mas só confirma em jogo real). Se
`SCALED` não engatar aceleração de hardware na máquina do usuário
(warning "no fast renderer available", visto no smoke test headless — lá
é esperado por não ter GPU real; na máquina do usuário NÃO deveria
aparecer), o fallback é voltar pra `vsync=0` (Adendo 3) ou expor o
toggle configurável.

**Adendo 5 (mesmo dia) — confirmado sem regressão + achado novo (zoom
causando rajada de reconstrução de cache):** usuário confirmou média de
frame estável na sessão com SCALED+vsync=1 (log de 3932 linhas, 8-10ms
consistente, sem tendência de piora — Adendo 4 validado). Pedido de
reanálise numa sessão seguinte (log novo, 3069 linhas, jogo reiniciado)
revelou um problema DIFERENTE e pré-existente (não introduzido pelo
vsync/SCALED): uma rajada de picos consecutivos (54/78/44/**157**/**95**/
58/47ms) com dois deles dominados quase inteiramente por UMA seção
(`rnd:TileRenderSystem`=141ms, `transform_scale`=78.9ms, sozinhas).

Causa raiz: `game.py::_handle_scroll_zoom()` escrevia `self._zoom`
diretamente a cada "clique" da roda do mouse. O bloco "Zoom surf:
dimensiona a world_surf" (`run()`, roda todo frame) recalcula o tamanho
lógico do mundo a partir de `self._zoom` e — sozinho, sem nenhuma chamada
externa — já invalida e reconstrói o cache de tiles sempre que esse
tamanho muda. Ou seja, CADA clique de scroll (não só o primeiro) já
disparava sua própria reconstrução completa por conta desse mecanismo
automático — um gesto normal de zoom (vários cliques em sequência rápida)
virava uma rajada de N reconstruções caras, uma por clique.

**Correção de rumo importante:** a primeira tentativa de fix (mesma
sessão) só debounceu a chamada EXPLÍCITA de `invalidate_cache()` dentro
de `_handle_scroll_zoom` — mas essa chamada já era redundante (o bloco
"Zoom surf" invalida sozinho todo frame que o tamanho muda), então esse
primeiro fix não teria resolvido nada de verdade — a análise do log
`rnd:TileRenderSystem`/`transform_scale` revelou a causa real ANTES do
fix errado ser reportado como pronto, e foi corrigido na mesma resposta.

**Fix (correto):** `_handle_scroll_zoom` não escreve mais em `self._zoom`
diretamente — acumula em `self._zoom_pending` (somando a partir do
pendente se já houver um debounce em andamento, não do valor antigo já
commitado) e arma `self._zoom_cache_dirty_timer = _ZOOM_DEBOUNCE_S`
(0.15s). O tick do debounce (`run()`, todo frame) só comita
`self._zoom = self._zoom_pending` quando o timer expira — SEM chamar
`invalidate_cache()` diretamente, deixando o bloco "Zoom surf" (que já
roda todo frame) detectar a mudança de tamanho e reconstruir sozinho,
exatamente 1x por gesto inteiro em vez de 1x por clique. Os outros 2
pontos que escrevem `self._zoom` direto (reset de zoom do God Mode/F10;
zoom por teclado `+`/`-`) continuam imediatos — não gestos de rajada,
sem necessidade de debounce — mas agora cancelam explicitamente
`self._zoom_cache_dirty_timer = -1.0` pra não deixar um debounce de
scroll pendente sobrescrever a mudança deles depois.

Validado: `py_compile`; suíte sem regressão (7F/85P); teste headless
isolado simulando 4 cliques de scroll em rajada (16ms entre cada, bem
dentro da janela de 150ms) — confirma `self._zoom` só muda (e o
"invalidate" só dispara) DEPOIS que o debounce estoura, uma única vez
pro gesto inteiro (4 cliques → 1 reconstrução, não 4).

**Não validado:** sessão manual em jogo real confirmando que a rajada de
picos ao dar zoom desaparece, e que o zoom ainda parece responsivo o
suficiente com o atraso de 150ms antes de "commitar" visualmente (trade-
off aceito: pequeno delay perceptível vs. rajada de travamento).

**Adendo 6 (19/07/2026) — CPU/GPU alto parado + câmera "tremendo",
mesmo sem cena pesada.** Usuário reportou consumo de >30% CPU / >20% GPU
com cena quase sem animação (retângulos se movendo), e "tremida" visual
ao seguir a câmera suavizada. Pesquisa na web (pygame docs, issue
#3085 do repo oficial, artigo "A Better Pygame Mainloop") + leitura do
`logs/client_prof.log` real do usuário confirmaram 3 causas
independentes:

1. **`clock.tick_busy_loop(FPS)` competindo com `vsync=1`**: a doc do
   pygame confirma que `tick_busy_loop` gira em loop ativo (SDL_delay
   busy-wait) só por precisão de timing — "usa muito CPU" por design.
   Com `SCALED`+`vsync=1` já ativo (Adendo 3/4 acima), `flip()` já
   bloqueia sozinho até o próximo refresh — rodar as duas técnicas de
   pacing ao mesmo tempo paga o mesmo controle de FPS duas vezes.
   **Fix**: `run()` trocou pra `clock.tick(FPS)` (sleep-based,
   `game.py`) — vsync continua garantindo a suavidade.
2. **Câmera "tremendo" — mismatch de pixel-snapping**: `TileRenderSystem`
   já arredondava o offset da câmera pra inteiro (só pra si mesmo, pra
   alinhar o cache de tiles), mas o offset usado pra posicionar
   sprites/personagens continuava em ponto flutuante puro — o fundo
   avança em saltos de pixel inteiro, os personagens deslizam em fração
   de pixel, e o descompasso entre os dois é que aparecia como
   "tremida" (classe de bug bem documentada em engines 2D com pixel
   art — Godot, GameDev.net). A suavização em si (`CameraSystem`, lerp)
   não era o problema. **Fix**: `cam_x`/`cam_y` agora são arredondados
   pra inteiro UMA VEZ em `run()` (`game.py`, ponto único que alimenta
   `system.render(cam_x, cam_y)` de todo mundo no frame) — fundo e
   sprites recebem sempre o MESMO valor já inteiro, mantendo o efeito
   de seguir suavemente (o lerp continua em float internamente) sem o
   descompasso visual.
3. **`profile_frames` preso em `true` no `config.json`**: `F11` só
   LIGAVA a flag (nunca desligava) — ao fechar o jogo, `_save_config()`
   persiste `profile_frames` no `config.json`, então um único F11
   apertado em qualquer sessão de dev ficava instrumentando ~40-45
   pontos por frame pra sempre, sem nenhum benefício. **Fix**: F11 agora
   é simétrico (`PROFILE_FRAMES = self._show_perf_overlay`, liga E
   desliga); `config.json` corrigido pra `false`.

Descartado por ora (identificado, mas risco/benefício desfavorável):
loop de envio de rede (`client/network.py::_send_loop`) faz polling a
200Hz (`asyncio.sleep(0.005)`) — já é sleep-based (não busy-wait, custo
real pequeno, comentário original já dizia "não queima CPU"); mudar
exigiria trocar o tipo da fila (`queue.Queue` thread-safe → handoff
pra `asyncio.Queue`), mais risco pra ganho marginal. `pygame.transform.
scale()` por frame (zoom): já otimizado (pula quando os tamanhos
batem, usa subsurface); custo inerente ao recurso de zoom, não vale a
pena remover a feature por causa disso.

Validado: `py_compile`; suíte completa 260/260 (sem cobertura client-side
de render — usuário confirma visualmente).

**Não validado**: sessão manual em jogo — CPU/GPU parado deve cair bem
depois da troca do tick; câmera não deve mais "tremer" ao seguir o
personagem; F11 duas vezes (ligar/desligar) não deve deixar
`profile_frames` preso em `true` no próximo fechamento do jogo.

**Follow-up (mesmo dia) — o fix da câmera (item 2) estava incompleto,
trocou um sintoma por outro.** Usuário testou: a "tremida" com a câmera
PARADA sumiu, mas agora, movendo o personagem, ele "salta de pixel em
pixel" em vez de deslizar suave. Print do Gerenciador de Tarefas
(Windows) mostrando ~22-38% CPU / ~22-28% GPU do processo do cliente —
na mesma faixa da queixa original, então a troca do `tick_busy_loop`
sozinha não bastou (a análise de causa continua válida — o
`tick_busy_loop` era CPU claramente desperdiçado — mas o "piso" de
CPU/GPU de uma pipeline `SCALED`+`vsync=1` renderizando/apresentando a
60 FPS parece ser real e não totalmente eliminável sem trocar de
arquitetura de apresentação, ex: FPS alvo menor ou renderização
assíncrona como no artigo "A Better Pygame Mainloop" — mudança maior,
não feita agora).

Causa raiz do "saltar de pixel": arredondar `cam_x`/`cam_y` pra inteiro
UMA VEZ (o fix original) forçava a câmera a só existir em posições de
pixel NATIVO inteiro — com o zoom padrão em 1.5x-2.5x, cada passo de 1
pixel nativo vira 1.5-2.5 pixels de TELA depois do
`pygame.transform.scale()`, um salto bem mais grosseiro e perceptível
que antes. A causa raiz de verdade não era "falta de arredondar" — era
`TileRenderSystem.render()`/`get_world_objects()`/
`render_static_objects()` arredondarem a câmera pra inteiro ANTES de
calcular a posição de desenho (`sub_x`/`scr_x`), descartando a fração
que deveria ter sido preservada. Fix correto: separar as duas coisas
que estavam misturadas — `tile_ox`/`tile_oy` (ÍNDICE de qual bloco de
tiles buscar no cache) precisa ser inteiro; a posição de DESENHO
(`sub_x`/`scr_x`) não — fica em ponto flutuante, derivada direto de
`camera_offset_x/y` (sem truncar antes), e só é arredondada pelo
`blit()` do pygame no final, o MESMO ponto onde entidades/personagens
sempre foram truncados. `game.py` voltou a passar `cam_x`/`cam_y` em
ponto flutuante puro (revertendo o "arredonda uma vez" do fix
anterior) — agora TODO consumidor (fundo, objetos estáticos como
árvore/arbusto, entidades) trunca no mesmo lugar (blit final), a partir
do mesmo valor fracionário, sem nenhum salto de pixel nativo artificial.

Validado: `py_compile`; suíte completa 260/260, rodada 3x.

**Não validado**: sessão manual — câmera parada continua sem tremer
(regressão do fix anterior) E movendo o personagem desliza suave, sem
saltar de pixel em pixel.

**Follow-up (mesmo dia) — a tremida "ao mover" sumiu, mas voltou a
tremer especificamente "quando o personagem para".** Usuário sugeriu
ajustar o tempo de suavização (`LERP_SPEED`) — palpite parcialmente
certo (mudar a velocidade muda o sintoma), mas a causa raiz é mais
funda: `CameraSystem.update()` usa decaimento exponencial
(`camera_position.x += (target.x - camera_position.x) * t`) — essa
fórmula matematicamente NUNCA chega EXATO no alvo, só se aproxima cada
vez menos, pra sempre. Enquanto o personagem anda, isso não se nota (a
câmera persegue com uma defasagem ~constante, o alvo também está se
movendo). Quando o personagem PARA, a câmera continua fazendo ajustes
MINÚSCULOS, decrescentes mas nunca zero, frame após frame — e como o
`transform.scale()` do zoom (1.5x-2.5x) amostra essa posição
ligeiramente diferente a cada frame, esse resíduo infinitesimal
aparece como tremor bem no instante de parar (mascarado durante o
movimento por um deslocamento bem maior). Clássico problema de câmera
com lerp em qualquer engine.

**Fix (padrão de indústria — "snap quando perto o bastante")**:
`CameraSystem.SNAP_EPSILON = 0.05` (px) — quando a distância restante
até o alvo fica abaixo disso, a câmera TRAVA exata na posição do alvo
em vez de continuar se aproximando pra sempre; acima do limiar,
continua suavizando normalmente (`LERP_SPEED` inalterado). Elimina o
resíduo interminável sem precisar mexer na velocidade de suavização —
ajustar só `LERP_SPEED` (a sugestão original) teria mudado a DURAÇÃO
da cauda residual, não eliminado ela.

Validado: `tests/test_client_ui.py` (2 testes) — câmera trava exata no
alvo quando a distância já está abaixo do epsilon; câmera continua
suavizando normalmente (não salta direto) quando longe do alvo. Suíte
completa 262/262, rodada 3x.

**Não validado**: sessão manual — câmera parada não tremer mais, e
personagem continuar deslizando suave ao mover (sem reintroduzir o
salto de pixel do fix anterior).

**Follow-up (mesmo dia) — SNAP_EPSILON não resolveu.** Usuário testou e
a tremida ao parar persistiu mesmo com o snap. Pedido do usuário:
desligar a suavização inteira, como passo de DIAGNÓSTICO — se a
tremida sumir sem nenhuma suavização, a causa é mesmo o lerp (ou algo
que só se manifesta através dele); se persistir mesmo assim, a causa é
outra coisa (candidato mais provável: a própria posição do personagem
não fica perfeitamente parada quando "parado" — ex. correção de
posição do servidor, ruído de ponto flutuante na interpolação de
tile — e nesse caso nenhum ajuste do lado da câmera resolveria).

Fix temporário: `CameraSystem.SMOOTHING_ENABLED = False` — quando
`False`, a câmera gruda direto na posição do alvo todo frame, sem lerp
nenhum (flag de instância, fácil de religar depois setando `True` —
não removi o código do lerp/SNAP_EPSILON, só desviei dele).

Validado: `tests/test_client_ui.py` ganhou o teste do modo sem
suavização (`test_camera_sem_suavizacao_gruda_direto_no_alvo`); os 2
testes de lerp/snap anteriores passaram a ligar `SMOOTHING_ENABLED`
explicitamente pra continuar cobrindo esse caminho, mesmo desligado por
padrão. Suíte completa 263/263, rodada 3x.

**Não validado**: sessão manual — se a tremida ainda aparecer mesmo SEM
nenhuma suavização, o próximo passo é investigar se a posição do
próprio personagem oscila quando parado (fora do escopo da câmera).

**Validado (19/07/2026, mesmo dia)**: usuário confirmou — sem
suavização, sem tremida nenhuma (parado E se movendo). Confirma que a
causa raiz era mesmo o lerp de decaimento exponencial nunca assentando
de verdade (a "cauda" residual descrita acima), não algo na posição do
personagem. **Decisão final**: suavização de câmera fica desligada
(`CameraSystem.SMOOTHING_ENABLED = False`) — coincide com a
preferência original do usuário antes de tentarmos consertar o lerp
("prefiro tirar o efeito"). Código do lerp/`SNAP_EPSILON` permanece no
arquivo (não é dead code de verdade — é uma decisão de feature
documentada, mesmo padrão de outros toggles do projeto), só não é mais
usado por padrão. Fecha a thread inteira de tremida de câmera (3
tentativas de fix + diagnóstico) e a de performance (Adendo 6) — ambas
JOGO-VALIDADAS nesta sessão.

---

### 30. Execução da auditoria arquitetural + REMOÇÃO DO MODO OFFLINE (15/07/2026)

Rodada de execução da auditoria da seção 11 de PROBLEMAS_ARQUITETURA.md —
detalhes, validações e plano dos restantes estão TODOS lá (§11 e
§11-EXECUÇÃO); esta entrada é o registro de decisão. 13 commits
(`5337805`..`aa1b1b6`).

**Decisões novas desta rodada:**
- **Modo offline REMOVIDO deste branch** (decisão do usuário — offline
  vive só no `rpg_ecs/` master). Entrada já era online-only; fases 1-2
  podaram: rage decay/mana regen/flecha de auto-attack/quests locais,
  saves locais (`_apply_save` deletado, `_autosave` sem `save_game`),
  `auto_start_quests` do boot. Fase 3 (dano local em spell_system +
  branches restantes) sai junto da unificação de handlers (§11 item 6).
  REGRA que continua valendo: em `SkillSystem`/`skill_handlers.py` o
  branch "offline" É o código do servidor — intocável até o item 6/7.
- **Suíte é sinal binário de novo**: 0 falhas toleradas (110/110 hoje).
  Testes de entry point rodam `server/main.py` E `main.py` como
  subprocess — regressão de import de script nunca mais passa batida.
- **Persistência de inventário é sanitizada na borda** (item A4) e
  **auth usa salt por conta** (item C1) — cliente inalterado nos dois.
- **Logs do servidor**: `server/log.py` (`RPG_LOG_LEVEL`, arquivo
  rotativo). **Flags de debug**: só env var (`RPG_DEBUG_*`).

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
| C→S | `CAST_DIR_UPDATE` | sid, dir_x, dir_y — direção final de skill direcional na conclusão do cast (não no keypress) | ✅ |
| S→C | `SKILL_RESULT` | caster_eid, sid, targets[{eid, damage, outcome, hp_after, applied_effects}] | ✅ |
| S→C | `STATS_UPDATE` | eid, hp, hp_max, xp_gained, rage, mana, heal_amount, heal_sid | ✅ |
| S→C | `SKILL_LEVELS_UPDATE` | levels{}, xp{} — snapshot completo, só ao dono (skill level Tibia-like) | ✅ |
| C→S | `QUEST_ACCEPT` | quest_id, npc_name — aceitar quest no diálogo do NPC | ✅ |
| C→S | `QUEST_TURN_IN` | quest_id, npc_name — entregar quest no diálogo do NPC | ✅ |
| S→C | `QUEST_UPDATE` | active{}, completed[], completed_qid — snapshot completo, só ao dono | ✅ |
| S→C | `ENTITY_SPAWN` | eid, kind, tx, ty, name, class_id, hp, hp_max, level, effects — mobs (`kind=enemy`) também levam `race, entity_class, tier, is_ranged, color, faction` (Sistema de Facções, 15/07/2026 — `content/faction_data.py`, cliente resolve disposição hostil/neutro/amigavel pra colorir a barra de HP) | ✅ |
| S→C | `ENTITY_DESPAWN` | eid (negativo para corpse) | ✅ |
| S→C | `LOOT_AVAILABLE` | corpse_id, tx, ty, items, coins — só ao dono | ✅ |
| C→S | `LOOT_REQUEST` | corpse_id | ✅ |
| S→C | `LOOT_RESULT` | corpse_id, items, coins | ✅ |
| S→C | `PLAYER_DEATH` | eid, corpse_tx, corpse_ty — corpo fica no local da morte | ✅ |
| C→S | `RELEASE_SPIRIT` | player clicou "Liberar espírito" — vira ghost no cemitério | ✅ |
| C→S | `REVIVE_REQUEST` | ghost perto do corpo clicou "Sim" — revive com 15% HP no corpo | ✅ |
| S→C | `PLAYER_REVIVE` | tx, ty, hp, hp_max, mana, max_mana — revive (cemitério ou corpo) | ✅ |
| S→C | `GHOST_STATE` | is_ghost, near_corpse, graveyard_timer — sync do estado do espírito | ✅ |
| C→S | `PLAYER_STAT_SYNC` | OBSOLETO — handler é no-op, servidor deriva stats de Equipment/TalentTree | ✅ |
| C→S | `EQUIP_SYNC` | equipment: {slot→item_dict} — enviado em equip/unequip; servidor reconstrói Equipment ECS, valida armor_class (`CLASS_ARMOR_ALLOWED`) e level_requirement por slot antes de aplicar | ✅ |
| S→C | `EQUIP_REJECTED` | slot, item_name, reason ("class"\|"level") — servidor recusou um slot do último EQUIP_SYNC (07/07/2026); cliente reverte slot pra bag e avisa | ✅ |
| C→S | `SAVE_STATE` | inventory, equipment, talents, skills, stats{gold, max_hp} | ✅ |
| C→S | `PING` / S→C `PONG` | client_ts / {client_ts, server_ts} | ✅ |
| C→S | `CHAT_SEND` / S→C `CHAT_MESSAGE` | text, channel, color | ✅ |
| S→C | `SOUND_EVENT` | kind, mob_eid, mob_name, tx, ty — aggro posicional | ✅ |
| S→C | `CAST_START` / `CAST_CANCEL` / `CAST_COMPLETE` | barra de cast visível | 🔲 |
| S→C | `PROJECTILE_SPAWN` / `PROJECTILE_HIT` | projéteis | 🔲 |
| S→C | `EFFECT_APPLIED` / `EFFECT_REMOVED` | status effects | 🔲 |
| S→C | `ENTITY_DEATH` | morte de player com animação/corpo no AOI — implementado p/ players (G3 mobs ainda 🔲) | ✅ |
| C→S | `ZONE_CHANGE_REQ` | `{to_map, target_x, target_y}` — player pisou em tile de transição | ✅ |
| S→C | `ZONE_CHANGE` | `{map_file, target_x, target_y}` — confirma troca; cliente executa `_do_transition` | ✅ |
| C→S | `ENTER_INSTANCE` | instâncias (dungeons/raids) — ver `zone_manager.py` (pendente) | 🔲 |
| C→S | `TRADE_REQUEST` | `{target_eid}` — Shift+clique num player remoto, botão "Trade" do popup | ✅ |
| S→C | `TRADE_INVITE` | `{from_eid, from_name}` — só ao alvo | ✅ |
| C→S | `TRADE_ACCEPT` / `TRADE_DECLINE` | `{}` — resposta ao convite pendente | ✅ |
| S→C | `TRADE_OPEN` | `{trade_id, other_eid, other_name}` — pros dois, ao aceitar | ✅ |
| C→S | `TRADE_OFFER_ITEM` | `{inv_index}` — oferta item da própria Inventory (clique direito na bag) | ✅ |
| C→S | `TRADE_WITHDRAW_ITEM` | `{offer_slot}` — retira item da própria oferta | ✅ |
| C→S | `TRADE_SET_GOLD` | `{amount}` — substitui o gold ofertado (débito/crédito da diferença) | ✅ |
| S→C | `TRADE_STATE` | `{trade_id, my_offer[], my_gold, their_offer[], their_gold, my_confirmed, their_confirmed}` — personalizado por lado, a cada mudança de oferta | ✅ |
| C→S | `TRADE_CONFIRM` | `{}` — confirma; executa quando os 2 lados confirmarem | ✅ |
| S→C | `TRADE_RESULT` | `{trade_id, received_items[], received_gold}` — pros dois, troca executada | ✅ |
| C→S | `TRADE_CANCEL` | `{}` — cancela a qualquer momento, devolve custódia dos 2 lados | ✅ |
| S→C | `TRADE_CANCELLED` | `{trade_id, reason}` — `declined\|cancelled\|distance\|disconnect\|inventory_full\|invalid` | ✅ |
| C→S | `DUEL_REQUEST` | `{target_eid}` — botão "Duelar" do modal de interação com player | ✅ |
| S→C | `DUEL_INVITE` | `{from_eid, from_name}` — só ao alvo | ✅ |
| C→S | `DUEL_ACCEPT` / `DUEL_DECLINE` | `{}` — resposta ao convite pendente | ✅ |
| S→C | `DUEL_START` | `{opponent_eid, opponent_name}` — pros dois; par vira hostil um ao outro | ✅ |
| S→C | `DUEL_END` | `{winner_eid, loser_eid, reason}` — `win\|declined\|distance\|disconnect\|invalid`; win = golpe letal deixou o perdedor com 1 HP (ninguém morre) | ✅ |
| C→S | `PARTY_INVITE` | `{target_eid}` — botão "Convidar p/ Grupo" do modal OU comando de chat `/convidar` | ✅ |
| S→C | `PARTY_INVITE_RECEIVED` | `{from_eid, from_name}` — só ao alvo | ✅ |
| S→C | `PARTY_INVITE_FAILED` | `{reason}` — só ao requester, convite recusado na origem (`invalid`/`declined`) | ✅ |
| C→S | `PARTY_ACCEPT` / `PARTY_DECLINE` | `{}` — resposta ao convite pendente | ✅ |
| S→C | `PARTY_STATE` | `{party_id, leader_eid, members:[{eid,name,class_id,level,hp,hp_max}]}` — a todos os membros a cada mudança; `party_id:-1, members:[]` individual pra quem saiu/foi expulso | ✅ |
| C→S | `PARTY_LEAVE` | `{}` | ✅ |
| C→S | `PARTY_KICK` | `{target_eid}` — só líder | ✅ |
| C→S | `CHAR_STATS_REQUEST` | `{}` — abrir modal de estatísticas (Fase E, 23/07/2026) | ✅ |
| S→C | `CHAR_STATS_DATA` | `{pve_damage, pvp_damage, mobs_killed, players_killed, duel_wins, duel_losses, arena_wins{}, arena_losses{}, quests_completed}` — só ao dono, sob demanda (não é push contínuo) | ✅ |

---

### 31. Nameplates: nome "Aventureiro", barra 1px curta, floating text atrás e sem contorno (15/07/2026)

3 bugs reportados pelo usuário na mesma leva, todos em UI de HUD/overlay
(sem protocolo novo):

- **Nome do próprio personagem sempre "Aventureiro"**: `CharacterStats.name`
  só era setado no caminho offline (removido na §30); no caminho online,
  `LOGIN_OK.char` já trazia `name` (coluna do banco, usado há tempos pelo
  SERVIDOR pra ENTITY_SPAWN/AOI de outros players), mas o handler do
  cliente (`client/network_handlers.py::_handle_msg_login_ok`) nunca
  aplicava esse campo na própria cópia local — só `class_id`. Fix: 1 linha
  (`char_stat.name = char.get("name") or char_stat.name`) logo depois do
  `class_id`.
- **Barras de XP/HP/recurso (player) e HP (mob) terminando 1px antes da
  borda direita**: `P_BAR_X1`/`M_HP_X1` em `ui/hud_bars.py` estavam 1px
  curtos do índice de pixel real da arte (61→62 numa arte de 64px;
  46→47 numa de 48px). Confirmado visualmente renderizando
  `build_player_hud`/`build_mob_hud` a 100% e ampliando 8x.
- **Floating text atrás dos nameplates + sem contorno**: causa raiz
  arquitetural — `FLT.render()` desenhava em **world-space**
  (`self._zoom_surf`, ANTES do flatten+scale pra `self.screen`), enquanto
  `WORLD_LABELS` (nameplates) já desenhava em **screen-space** (depois do
  flatten). O floating text ficava "cozido" dentro da imagem do mundo
  antes dos nameplates desenharem por cima — não dava pra resolver só
  reordenando chamadas, precisava mudar de espaço de coordenadas (mesma
  classe de problema já resolvida pro nome/HUD em 23.5/23.9). Fix:
  `FloatingTextManager.render()` ganhou parâmetro `zoom`, converte
  `wx/wy` (mundo) pra tela (`(wx - cam_x) * zoom`) internamente, e a
  chamada em `game.py` moveu de antes do flatten pra **depois** de
  `WORLD_LABELS.render(...)`, alvo `self.screen`. Contorno: helper novo
  `_render_outlined()` (8 blits do texto em preto ao redor + 1 blit da
  cor normal no centro), cacheado por `(id(font), text, color,
  outline_color, thickness)` — `FloatingTextEntry` perdeu o slot `surf`
  cacheado por entrada (não fazia mais sentido: tamanho efetivo da fonte
  agora depende do zoom da câmera, que pode mudar durante a vida do
  texto).

**Validado:** suíte completa sem regressão (118/118); `py_compile` nos 4
arquivos tocados; script headless renderizando `build_player_hud`/
`build_mob_hud` a 100% confirmando fill até a borda (sem gap); script
headless confirmando que o composto outlined tem pixels pretos na borda
E pixels da cor original no centro, e que `FLT.render()` desenha por cima
de um retângulo opaco simulando um nameplate no mesmo ponto de tela.
Payload `char_data` do servidor confirmado (via grep) sempre incluindo
`name` antes de virar `LOGIN_OK.char`.

**Não validado (na época):** sessão manual em jogo real — confirmado
posteriormente pelo usuário ("Funcionou perfeitamente").

**Correção (ver §33):** o ajuste de `P_BAR_X1`/`M_HP_X1` descrito acima
estava ERRADO pro caso do mob (46→47 avançou 1px demais, sobre a própria
borda) — só coincidentemente certo pras barras de HP/recurso do player.
Raiz real do "1px curto" original: os 3 pares X0/X1 eram compartilhados
entre barras cujo asset tem bordas em colunas DIFERENTES (mapeado por
scan pixel a pixel em §33, não "de olho" como da primeira vez).

---

### 32. Mover-se cancelava o auto-attack de guerreiro/mago — generaliza a exceção do arqueiro (can_kite) pra todas as classes (15/07/2026)

Bug reportado pelo usuário: atacando um mob e movendo o personagem
(teclado), o guerreiro parava de atacar — precisava re-clicar/re-selecionar
o alvo pra retomar, mesmo com o mob ainda perseguindo/alcançando o player.
Comportamento esperado: mover NUNCA desliga o auto-attack; só deselecionar
o alvo (TAB, clique vazio, alvo morto/fora de visão) deve parar.

Causa raiz: `PlayerInputSystem.update()` (`ui/systems.py`, bloco de
movimento por teclado) setava `combat_state.is_pursuing = False`
incondicionalmente a cada passo de movimento manual — EXCETO se
`combat_stats.can_kite` fosse `True`, uma flag que só o arqueiro tinha
(`CLASS_MELEE_OVERRIDES["arqueiro"]`, stats_system.py), criada
originalmente pra permitir "kitar" (atirar enquanto anda). Guerreiro e
mago nunca tiveram essa flag, então todo passo de movimento os tirava do
modo perseguição — o mesmo bug que o arqueiro já teve e já tinha sido
corrigido, só que a correção nunca foi generalizada pras outras classes.

Fix: removida a checagem `can_kite` inteira — `is_pursuing` nunca mais é
setado como `False` por movimento de teclado, pra nenhuma classe (só
`auto_move`/path de clique-de-chão é cancelado, que é o comportamento
correto de "teclado assume controle manual"). Como isso deixa `can_kite`
sem nenhum consumidor (confirmado via grep — só existia essa 1 leitura),
removida também a flag em si: `CombatStats.can_kite` (`engine/
components.py`) e a entrada `"can_kite": True` em `CLASS_MELEE_OVERRIDES`
(`engine/stats_system.py`).

Sem mudança de protocolo — é lógica 100% client-local de input
(`PlayerInputSystem` só processa a entidade com `PlayerControlled`); o
servidor já era agnóstico a isso (`combat_processor.py::
_process_player_attacks` só olha `CombatState.target_entity_id` +
`is_pursuing` + range, nunca cancela por movimento).

**Validado:** suíte completa sem regressão (118/118); `py_compile`. Não
validado em sessão manual de jogo real com mob de verdade (mesma limitação
de sempre pra fixes de combate — pedido explícito de validação visual do
usuário fica pendente).

---

### 33. Correção do fix de barras (§31): borda do asset não é reta — X1 único por bar-type causava overflow (15/07/2026)

Usuário reportou regressão: a barra de HP do MOB (após o fix de §31) agora
passava 1px da conta, pintando por cima da própria borda direita e
"sumindo" o contorno. Causa raiz: o fix de §31 tinha sido validado só
visualmente (screenshot ampliado, "olho"), sem inspecionar os pixels reais
do PNG — assumiu que a borda de cada asset era uma coluna reta única e
girou `P_BAR_X1`/`M_HP_X1` pra "o último índice válido da arte" (63→62
convertido pra base nativa: 62; 47 pro mob), quando o correto é "o último
pixel de INTERIOR antes da borda", que **varia por linha** nesses 2 PNGs
(bevel/contorno não é geometricamente reto).

Scan pixel a pixel real (`pygame.Surface.get_at()`, script headless,
15/07/2026) do último pixel de interior (opaco, não-preto) por linha:

| Asset | Linha(s) | Último pixel de interior |
|---|---|---|
| `player_hud_bar.png` (64×16) | XP (y=3) | **x=61** |
| `player_hud_bar.png` | HP (y=5..8) | **x=62** |
| `player_hud_bar.png` | Recurso (y=10..11) | **x=62** |
| `mob_hud_bar.png` (48×12) | HP (y=5..6) | **x=46** |

Ou seja: o fix de §31 estava CORRETO para HP/recurso do player (62 é
certo) mas ERRADO pra XP do player (61, não 62) e pro HP do mob (46, não
47) — coincidência de 2 dos 3 valores terem ficado certos escondeu o erro
até o usuário notar visualmente na barra do mob (mais visível em combate
que a XP, cuja cor roxa contra fundo escuro disfarça o overflow de 1px).

Fix: `ui/hud_bars.py` ganhou um par dedicado `P_XP_X0, P_XP_X1 = 16, 61`
(só pra XP; `P_BAR_X0/X1 = 16, 62` continua servindo HP+recurso, já
estava certo) e `M_HP_X1` revertido de 47 para **46**.

**Validado** (desta vez com inspeção de pixel, não só visual): script
headless que (1) escaneia `x1_native+1` de cada barra nas 4 combinações
acima e confirma que o pixel é preto/borda intacta, e (2) confirma que o
fill a 100% realmente alcança `x1_native` (não regride pro bug original de
"1px curto"). As 4 combinações passam nos 2 checks. Suíte completa sem
regressão (118/118). `py_compile`.

**Lição:** validação visual de "olho" em screenshot ampliado não pega
erro de 1px de forma confiável, mesmo ampliado 8x — o próximo ajuste de
coordenada de asset pixel-art deve usar scan automatizado
(`get_at()` por linha) em vez de inspeção visual, como feito aqui.

**Não validado:** sessão manual em jogo real.

---

### 34. Sistema de Facções (hostil/neutro/amigável) + NPCs de combate — Fase 1: infraestrutura (15/07/2026)

Início de uma feature grande, planejada em fases (plano completo salvo
pré-implementação, ver processo abaixo). Motivação do usuário: hoje todo
mob é incondicionalmente hostil (agroa só por proximidade) e NPC
(vendedor/quest-giver/treinador/ferreiro) é uma entidade 100% estática sem
nenhum sistema de combate — não existe qualquer conceito de facção,
hostilidade ou relação entre entidades no código (confirmado por grep
amplo antes de começar: zero ocorrências de "hostile/neutral/faction/
disposition" fora de docs não relacionadas). Pedido: mobs hostis (agroam
por proximidade) vs. neutros (só brigam se atacados, revertem depois —
estilo WoW), e no fim das fases, NPCs/mobs de facções diferentes brigando
entre si sem depender do player como intermediário. Requisito explícito
do usuário: nenhum puxadinho — a base tem que ser genérica o bastante pra
sustentar isso sem gambiarra por par de entidade.

**Processo**: pesquisa no código (mapeamento completo de como NPC/Mob
funcionam hoje, incl. state machine completa do `AIControlled` da
`EnemyAISystem`) + pesquisa web sobre o sistema de reputação/aggro do WoW
(tiers Hostil/Neutro/Amigável, mecanismo de "co-aggro"/assist entre
aliados da mesma facção — fontes:
[Reputation](https://wowpedia.fandom.com/wiki/Reputation),
[Hostile](https://vanilla-wow-archive.fandom.com/wiki/Hostile),
[Neutral](https://vanilla-wow-archive.fandom.com/wiki/Neutral),
[Aggro radius](https://wowpedia.fandom.com/wiki/Aggro_radius)) + plano
formal em modo de planejamento, com 3 decisões de design confirmadas
explicitamente com o usuário antes de escrever qualquer código:
1. Modelo de facção = **facções nomeadas + matriz de relação entre cada
   par** (não um binário hostil/neutro só relativo ao player) — é o único
   jeito de expressar "bandido ataca guarda" sem hardcode por par.
2. Só um **novo arquétipo** de NPC de combate herda a infraestrutura de
   mob — vendedor/quest-giver/ferreiro/treinador continuam exatamente
   como são, só ganham nameplate por consistência visual (Fase 3).
3. Entrega **em fases** independentes, cada uma testável sem regressão na
   suíte, em vez de uma implementação monolítica.

**Fase 1 (esta entrada) — infraestrutura pura, sem mudar comportamento
nenhum**:
- `content/faction_data.py` (novo): tabela estática `RELATIONSHIP` — par
  de facção → tier (`"hostil"`/`"neutro"`/`"amigavel"`), busca simétrica
  via `get_relationship()`, default `"neutro"` pra par não listado.
  Facções seed: `monstros_hostis`, `vida_selvagem`, `guardas_vila`,
  `bandidos`, `jogadores` (`PLAYER_FACTION`).
- `engine/faction_system.py` (novo): `get_entity_faction()`,
  `get_relationship_between()`, `can_engage()`, `is_hostile()` — únicos
  pontos que `EnemyAISystem`/`CombatSystem` vão consultar nas próximas
  fases (nunca reimplementar a lógica inline num call site).
- `engine/components.py`: novo componente `Faction(faction_id)`. Player
  NÃO ganha o componente — facção resolvida via `PlayerControlled` +
  constante, pra não tocar em nenhum call-site de spawn de player nem no
  formato de save.
- `SpawnZone`/`Faction`/`create_enemy()` (`engine/components.py`,
  `engine/entity_factory.py`) — default `"monstros_hostis"` (**correção
  feita ainda durante a Fase 2**, ver abaixo — o default original desta
  entrada era `"vida_selvagem"`, que é NEUTRO com o player; como 100% dos
  mobs hoje são hostis, esse default teria revertido silenciosamente todo
  mob não migrado pra neutro assim que a Fase 2 passasse a consultar o
  campo — o oposto de retrocompatível. Corrigido pra `"monstros_hostis"`
  antes de qualquer sistema de jogo consultar o campo de verdade).
- `create_enemy()`/`create_spawn_zone()` (`engine/entity_factory.py`)
  ganham parâmetro `faction` opcional, repassado a `Faction()`.
  `SpawnZoneSystem._spawn_one()` (`engine/world_systems.py`) passa
  `faction=zone.faction`.
- **Ponto de achatamento real encontrado**: `engine/map_loader.py`
  (~linha 296-311) reconstrói um dict NOVO a partir do JSON do mapa,
  descartando qualquer chave não listada explicitamente — sem adicionar
  `"faction"` nessa lista, o campo nunca chegaria de
  `{mapa}_entities.json` até `_create_spawn_zones_for_map`
  (`server/world_server.py`), mesmo com todo o resto pronto. Corrigido.
- Ainda **dead code** nesta fase: `EnemyAISystem`/`CombatSystem` não
  consultam facção — todo mob continua se comportando exatamente como
  antes (hostil por proximidade, sem exceção). Migração de dados dos 3
  mapas existentes (só 21 zonas de spawn no total, escopo pequeno) fica
  pra Fase 2, junto com o comportamento hostil/neutro de verdade.

**Validado**: `tests/test_faction.py` (novo, 14 testes) — tabela-verdade
de `get_relationship()`/`can_engage()`/`is_hostile()` contra entidades
dummy num `World` isolado (sem rodar nenhum sistema de jogo). Suíte
completa sem regressão (132/132, era 118 + 14 novos). `py_compile` em
todos os arquivos tocados.

**Próximas fases** (plano completo em
`C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`, será
copiado pra este arquivo conforme cada fase fecha): Fase 2 (✅ ver §34.1
abaixo), Fase 3 (nameplate de NPC — badge de
nível igual ao mob, sem barra de HP), Fase 4 (novo arquétipo de NPC de
combate + componente `Combatant` genérico pro gate de sync
`server/world_server.py:2940`, hoje restrito a `Enemy`), Fase 5 (combate
multi-tipo faccionado de verdade — `_select_target()` deixa de assumir
`PlayerControlled`, assist/co-aggro entre aliados).

---

### 34.1 Sistema de Facções — Fase 2: hostil/neutro em relação ao player (15/07/2026)

**Correção de design feita ANTES de qualquer sistema consultar o campo**:
o default `faction="vida_selvagem"` escolhido na Fase 1 (§34) era neutro
com o player — como 100% dos mobs hoje são hostis, esse default teria
revertido silenciosamente todo mob não migrado pra neutro assim que
`EnemyAISystem` passasse a consultar, mudando o balanceamento do jogo sem
intenção. Trocado pra `"monstros_hostis"` em TODOS os pontos (componente
`Faction`, `create_enemy()`, `create_spawn_zone()`, `SpawnZone`,
`_create_spawn_zones_for_map`, `engine/map_loader.py`, `game.py`) —
retrocompatível de verdade: zona/mob não migrado se comporta EXATAMENTE
como antes.

**Migração de dados**: os 3 mapas existentes (21 zonas de spawn, contadas
por leitura direta) ganharam `"faction": "monstros_hostis"` explícito em
cada `spawn_zone` do JSON (`maps/{map_1,cave_east,cave_west}_entities.json`)
— migração de conteúdo SEM MUDANÇA DE COMPORTAMENTO (só torna explícito o
que já era o default). Inserção cirúrgica via regex ancorada em
`"respawn_cooldown"` (única por zona, confirmado por contagem antes de
editar) — evita reserializar o JSON inteiro e gerar diff gigante de
reformatação (primeira tentativa, com `json.dump`, gerou 296 linhas de
diff pra 10 zonas só por causa de reformatação de arrays; revertida e
refeita cirurgicamente). Qual raça vira neutra de verdade (ex: lobo) é
decisão de design/balanceamento do usuário, não tomada aqui — fica pra um
pedido futuro explícito.

**Mudança de comportamento (2 gates, não 1)**: a primeira tentativa só
gateou a transição `IDLE→AGGRO_DELAY` por proximidade
(`EnemyAISystem.update()`, bloco "Detecção inicial") com
`is_hostile(world, mob_eid, target_eid)`. Suíte nova (`tests/
test_faction.py`) pegou um SEGUNDO caminho que ignorava esse gate por
completo: `in_attack_range` (mesma função, ~linha 1797) já é `True` pra
QUALQUER mob adjacente ao alvo independente do `AIControlled.state`
(inclusive `IDLE` "de nascença" — comportamento pré-existente e
INTENCIONAL, documentado inline como fix de uma regressão real anterior:
sem isso, um mob que nasce adjacente ao player travava em IDLE pra
sempre). Esse `in_attack_range` alimenta TANTO a promoção de estado pra
`ATTACKING` quanto a execução de verdade do ataque (`deal_damage`/cast
ranged) — um mob neutro adjacente a um player atacava imediatamente,
nunca tendo passado pelo gate de aggro. Fix: `in_attack_range` ganhou mais
uma cláusula — `ai_control.state != "IDLE" or is_hostile(...)` — mob
hostil continua atacando direto de IDLE (comportamento antigo intocado);
mob neutro/amigável só ataca depois de sair de IDLE por um motivo
legítimo (aggro por dano, que já move o mob pra `AGGRO_DELAY` no mesmo
tick em que acontece — nunca fica "IDLE com alvo válido provocado").

**Validado**: `tests/test_faction.py` ganhou `TestProximityAggroByFaction`
(4 testes novos, 18 no arquivo): regressão (mob hostil ainda agroa por
proximidade — usa `WorldServer` real + `EnemyAISystem.update()` de
verdade, não só os helpers puros), mob neutro ignora proximidade, mob
neutro agroa ao ser atacado (via `_process_player_attacks`, mesmo caminho
real do servidor — precisou de hit garantido, `acerto=100`+
`dodge_rating=parry_rating=0`, senão o teste era flaky por RNG de acerto,
pego rodando 5x em sequência), mob neutro agroado por dano ainda respeita
leash/RETURNING (prova que facção não interfere no mecanismo de evasão já
existente — reaproveitado sem alteração). Suíte completa 136/136 (132 +
4), rodada 2x em sequência pra descartar flakiness residual de RNG em
outros testes que compartilham o mesmo código de combate.

**Não validado:** sessão manual em jogo real com um mob de fação neutra
de verdade.

**Conteúdo de teste (15/07/2026, a pedido do usuário)**: as 5 zonas de
Lobo em `maps/map_1_entities.json` viraram `faction: "vida_selvagem"`
(neutro com o player) — única mudança de balanceamento real desta rodada,
feita explicitamente pra dar ao usuário algo pra testar ao vivo (todo o
resto da Fase 2 preserva o comportamento anterior). Reversível a qualquer
momento voltando pra `"monstros_hostis"`. Demais raças/zonas continuam
hostis.

---

### 34.2 Identificação visual de disposição — barra de HP por cor (hostil/neutro/amigável) (15/07/2026)

Pedido do usuário logo após testar o Lobo neutro pela 1ª vez: "quero criar
uma identificação de mobs hostis e neutros, os hostis a barra de HP é
vermelha, neutros é amarela clara, e os NPCs que forem amigáveis(friendly)
terão a barra verde". Implementado como função pura da DISPOSIÇÃO (tier
`hostil`/`neutro`/`amigavel` de `content/faction_data.py::get_relationship()`),
não do tipo de entidade — qualquer mob cuja facção resolva `amigavel`
também fica verde, não só um futuro NPC de combate (Fase 4).

- `ui/hud_bars.py`: `DISPOSITION_HP_COLORS = {"hostil": (200,40,40),
  "neutro": (235,220,110), "amigavel": HP_COLOR}` (amigável reaproveita o
  verde de sempre). `build_mob_hud()` ganha parâmetro `hp_color` (default
  `HP_COLOR`, mantém os 2 call sites antigos — testes e o caminho morto de
  `ui/systems.py` — funcionando sem mudança).
- **Protocolo**: `server/world_server.py::_build_mob_spawn_payload()`
  (única função por trás de `WORLD_STATE`/`ENTITY_SPAWN`/`AOI_UPDATE`
  spawned — confirmado, sem caminho duplicado) ganha campo `"faction"` no
  payload, lido do componente `Faction` do mob (nunca existia antes —
  cliente não tinha NENHUMA info de facção). Documentado na tabela de
  mensagens (linha `ENTITY_SPAWN`).
- Cliente: `client/remote_entity_handlers.py::_spawn_remote_mob()` passa
  `faction=data.get("faction", ...)` pro `create_enemy()` que já roda ali
  — reaproveita o componente `Faction` que `create_enemy()` já anexa
  (nenhum campo novo em `RemoteEntityMeta`, evita duplicar o mesmo dado em
  2 lugares). `_draw_mob_hp_bars()` resolve `get_relationship(Faction.
  faction_id, PLAYER_FACTION)` e passa a cor pra `build_mob_hud()`.

**Validado**: suíte completa 136/136. Script headless renderizando as 3
disposições lado a lado (`build_mob_hud` com cada `DISPOSITION_HP_COLORS`)
confirmando visualmente vermelho/amarelo-claro/verde; e resolução real de
`get_relationship()` pras 3 facções de conteúdo existentes (Lobo=neutro,
Zumbi=hostil, guardas_vila=amigável, essa última ainda sem uso em nenhuma
zona real — só a tabela).

**Validado em jogo real pelo usuário** (15/07/2026): "deu certo".

---

### 34.3 Nameplate de NPC — badge de nível, sem barra de HP (Fase 3, 15/07/2026)

NPC não-combatente (vendedor/quest-giver/ferreiro/treinador) só tinha um
texto solto com o nome (`WORLD_LABELS.add_text`, sem nenhuma arte) —
pedido do usuário: "NPCs também quero que tenham nameplates igual aos
mobs". Como NPC não tem `CombatStats`, uma barra de HP não faz sentido —
só o badge (círculo + número de nível) faz.

- `ui/hud_bars.py::build_npc_badge(level, level_font)` (novo): recorta só
  a região do círculo de nível (`_NPC_BADGE_W = 12`px) do MESMO asset
  `mob_hud_bar.png` que os mobs usam — antes da coluna divisória sólida
  que separa o badge da barra de HP (confirmado no mapeamento pixel a
  pixel da rodada anterior, §31/33) — sem duplicar arte nem criar PNG
  novo. Reaproveita `_level_center_px`/`_blit_level_number` já existentes.
- `ui/systems.py` (bloco "Nameplate de NPC", antes só `WORLD_LABELS.
  add_text`): agora enfileira `build_npc_badge(...)` via `WORLD_LABELS.
  add_icon` (mesmo padrão do mob/player) + o nome via `add_text` com
  `gap_before=2` — mesmo `stack_key=entity_id` de sempre, então o ícone
  de quest (`QuestDialogSystem.render_world`) continua empilhando
  corretamente por cima.
- **Não é caminho morto**: diferente do bloco de mob HP-bar na mesma
  função (morto em modo online — mob remoto usa
  `client/remote_entity_handlers.py`), NPCs não-combatentes SEMPRE são
  entidades locais client-side (`game.py`, spawnadas direto do JSON do
  mapa — nunca sincronizadas pelo servidor) — este código roda de
  verdade em modo online.

**Validado**: script headless comparando `build_npc_badge` lado a lado
com `build_mob_hud` (mesmo círculo/número, visualmente idêntico) +
verificação pixel a pixel de que nenhuma cor de barra de HP vaza pro
recorte do badge. Suíte completa 136/136.

**Não validado**: sessão manual em jogo real (nameplate de vendedor/
quest-giver/treinador/ferreiro com o badge novo, empilhamento do ícone de
quest por cima).

---

### 34.4 Novo arquétipo de NPC de combate + componente `Combatant` (Fase 4, 15/07/2026)

Objetivo: NPC de combate (guarda, etc.) precisa da MESMA infraestrutura
de IA/combate/sync que mob já tem, mas com identidade de NPC (não
`Enemy`) e facção tipicamente amigável/neutra. Reaproveitar em vez de
duplicar — mesmo espírito de "nada de puxadinho" do pedido original.

- `engine/entity_factory.py`: corpo de `create_enemy()` (tudo — Position/
  Renderable/Collider/`AIControlled`/`InitialPosition`/`DetectionRadius`/
  `TileMovement`/`EnemyTier`/`CombatStats`/`EntityIdentity`/`Faction`/
  `EnemyAbilities` — a lógica inteira de resolução de atributos por
  mob_definitions/tier/level) fatorado num helper privado
  `_build_combat_entity(...)`, que retorna a entidade SEM tag de tipo.
  `create_enemy()` vira wrapper fino (`_build_combat_entity(...)` +
  `Enemy()`). Novo `create_combat_npc(world, tile_x, tile_y, faction,
  name="", profession="Guarda", ...)` — mesmo helper + `NPC(name, level,
  profession)` no lugar de `Enemy`. Ganha também `identity_name`
  (parâmetro novo em `_build_combat_entity`) pra dar nome próprio ("Guarda
  Real") em vez de mostrar só a raça genérica.
- Novo componente marcador `Combatant()` (`engine/components.py`),
  anexado por `_build_combat_entity()` — mob E NPC de combate carregam.
  `server/world_server.py` (detecção de novas entidades pra `_mob_eids`/
  `ENTITY_SPAWN`, tick principal): gate trocado de `Enemy` pra
  `Combatant` — `Enemy` sozinho implicaria "hostil ao player", falso pra
  um NPC de combate amigável. Retrocompatível: todo `Enemy` sempre
  implica `Combatant` (mesma função os anexa os dois).
- **Achado durante o refactor**: `create_training_dummy()` (função
  separada, não passa por `_build_combat_entity`) anexava `Enemy()`
  direto — sem `Combatant`, o boneco de treino pararia de sincronizar
  pro cliente assim que o gate mudasse. Corrigido (ganhou `Combatant()`
  também).
- **Peça da Fase 5 trazida pra cá**: `apply_damage_core()`
  (`engine/core_systems.py`) ganha o gate `can_engage(world, killer_eid,
  target_id)` (novo retorno `"blocked_friendly"`, mesmo padrão de
  `blocked_dead`/`blocked_immune`/`blocked_evade`) — só checado quando
  `killer_eid != -1` (DoT/ambiente sem atacante identificado não tem
  facção pra resolver). Sem isso, um NPC de combate "amigável" recém-
  criado seria livremente matável, contradizendo a própria palavra —
  shippar a Fase 4 sem essa proteção teria sido um estado
  visivelmente quebrado. O resto da Fase 5 (feedback visual do bloqueio,
  bloqueio de efeitos secundários tipo knockback/DoT, e principalmente
  `_select_target()` deixar de assumir `PlayerControlled` — combate
  NPC-vs-NPC/mob-vs-NPC de verdade) continua isolado, não implementado
  aqui.
- Spawn de conteúdo real (colocar um guarda de verdade num mapa) fica
  pra depois — Fase 4 entrega só a CAPACIDADE (função + componente +
  gate de sync), provada por teste, sem adicionar NPC de combate a
  nenhum mapa ainda.
- Documentação: `COMPONENTES_ECS.md` (entradas `Faction`/`Combatant`,
  faltavam desde a Fase 1 — corrigido agora) e `MAPA_PROJETO.md`
  (`content/faction_data.py`, `engine/faction_system.py`, tabela "onde
  encontrar o quê").

**Validado**: `tests/test_faction.py` ganhou `TestCombatNpcArchetype` (5
testes, 23 no arquivo): `create_combat_npc` tem `Combatant`+`Faction`+
`NPC`+`CombatStats` e NÃO tem `Enemy`; registra em `_mob_eids` via
`Combatant` depois de 2 ticks reais; `_build_mob_spawn_payload` inclui
`faction`/`name` corretos; NPC de combate amigável não perde HP ao ser
atacado (`deal_damage` com `pre_outcome="hit"`, determinístico — sem
RNG); regressão: NPC de combate de facção HOSTIL (bandido) continua
recebendo dano normalmente (gate não é geral demais). Suíte completa
141/141 (136 + 5), rodada 3x pra descartar flakiness.

**Não validado (na época)**: sessão manual em jogo real — ver §34.5, o
usuário pediu conteúdo de teste real logo em seguida.

---

### 34.5 Conteúdo real: loader de `combat_npcs` + guarda de teste (15/07/2026)

Usuário pediu uma forma de validar a Fase 4 ao vivo. Em vez de um hack
descartável, implementado o loader de conteúdo real que qualquer NPC de
combate (guarda, etc.) vai precisar de qualquer forma — mesmo padrão já
usado por `merchants`/`quest_givers`/`spawn_zones`:

- `engine/map_loader.py::_merge_entities_json`: nova chave JSON
  `"combat_npcs"` (lista de `{x, y, faction, name, profession, race,
  entity_class, level, tier, is_ranged}`, todos opcionais exceto x/y).
- `server/world_server.py::_create_combat_npcs()` (novo, chamado em
  `_load_map_for` junto com spawn_zones/training_dummies): lê a lista e
  chama `create_combat_npc()` por entrada — diferente de
  `_create_npc_blockers` (entidade mínima só pra pathfinding), o NPC de
  combate já nasce com todos os componentes reais, sem precisar de
  blocker separado.
- `maps/map_1_entities.json`: 1 entrada de teste — "Guarda Real"
  (`guardas_vila`, nível 10) em `(117, 388)`, perto do spawn do player
  `(115, 389)` e dos bonecos de treino. Reversível — remover a entrada
  de `combat_npcs` some com ele.

**Regressão real pega ao adicionar o conteúdo**: rodar a suíte completa
DEPOIS de adicionar o guarda de teste (não só depois do código da Fase
4) quebrou 16 testes, de forma determinística nas 3 repetições — `tests/
helpers.py::first_mob()`/`first_ai_mob()` (usados por boa parte da
suíte de combate) só pulavam `TrainingDummy`, então passaram a pegar o
"Guarda Real" no lugar de um mob hostil de verdade. Como a facção do
guarda (`guardas_vila`) é `amigavel` com o player, todo `deal_damage`
contra ele nos testes era bloqueado pelo gate novo de `apply_damage_core`
(§34.4) — HP nunca mudava, e os testes que esperavam dano/morte falhavam.
Fix: `first_mob()`/`first_ai_mob()` passaram a pular também qualquer
entidade com componente `NPC` (não só `TrainingDummy`) — "mob" nesses
helpers sempre quis dizer "criatura hostil `Enemy`-tagged", nunca um NPC
de combate amigável, e agora essa distinção existe de verdade no código.

**Lição**: testar Fase 4 "só com testes automatizados" não é suficiente
depois que CONTEÚDO REAL entra no mapa — helpers de teste que assumem
"todo mob em `_mob_eids` é hostil" (verdade até a Fase 4 existir) podem
quebrar silenciosamente. Vale reconferir a suíte completa toda vez que
conteúdo real (não só código) mudar, mesmo que o código em si já estivesse testado.

**Validado**: smoke-test manual confirmando o guarda spawna, entra em
`_mob_eids`, facção correta, payload de `ENTITY_SPAWN` com `name`/
`faction` certos. Suíte completa 141/141, rodada 3x, DEPOIS da correção
dos helpers.

**Não validado (na época)**: sessão manual em jogo real — ver §34.6, o
usuário testou e achou 2 bugs reais na proteção "amigável".

---

### 34.6 Correção real: bloquear só o dano final não bastava — combate nem deveria começar (15/07/2026)

Usuário testou o "Guarda Real" e relatou 2 bugs: (1) atacá-lo iniciava
combate normalmente, golpes "erravam" em loop (dano sempre 0, sem
feedback claro do porquê); (2) depois disso, o guarda passava a
**perseguir** o player, como se estivesse em combate — claramente errado
pra uma facção amigável. Causa raiz: o gate de §34.4
(`apply_damage_core` bloqueando a escrita final de HP) só cobria UM
sintoma — nada impedia o combate de sequer COMEÇAR, e existiam **outros
3 blocos duplicados** de "aggro por dano" (fora do que já tinha sido
corrigido no aggro por proximidade da Fase 2) que nunca checavam
facção, disparando incondicionalmente sempre que `attacker_is_player`:
`CombatSystem.deal_damage` (melee, `engine/world_systems.py`),
`_server_apply_magic_damage`/`_server_apply_ranged_physical`
(`server/spell_completion_processor.py`), e `_apply_magic_damage`
(`ui/spell_system.py`, client-side).

Fix em 2 camadas (gate na origem + rede de segurança, mesmo padrão já
usado nesta feature):
1. **Origem — nem entra em combate**: `WorldServer.set_player_target()`
   (`server/world_server.py`) passa a recusar `target_eid` de facção
   `amigavel` — `can_engage()` checado ANTES de aceitar o alvo. Sem alvo
   válido, `_process_player_attacks` nunca tenta atacar (não há golpes
   "errando" em loop).
2. **Rede de segurança**: `combat_processor.py::_process_player_attacks`
   ganha o mesmo gate (limpa `target_entity_id` se, por qualquer via, um
   alvo amigavel chegar até ali) — mesmo padrão do check de "alvo morto"
   já existente ali do lado. E os 4 blocos de aggro-por-dano (o de
   `deal_damage` + os 3 recém-descobertos) ganham `can_engage(attacker,
   target)` no `if`, ao lado do `state == "IDLE"` — nenhum deles seta
   `AGGRO_DELAY`/`CHASING` contra alvo amigavel, mesmo que o dano em si
   já estivesse bloqueado em outro lugar.
- **Não corrigido** (fora do escopo deste bug report): o bloco de
  colisão de knockback do Tiro Repulsivo (`ui/spell_system.py`, ~linha
  1230) também seta aggro sem checar facção — não foi tocado porque
  exigiria rastrear a identidade do atacante original num contexto onde
  ela não está prontamente disponível; fica anotado pra Fase 5 (que já
  vai revisitar todos os efeitos secundários de skill contra alvo
  faccionado).

**Validado**: 2 testes novos em `tests/test_faction.py`
(`test_set_player_target_recusa_alvo_amigavel`,
`test_auto_attack_completo_nao_faz_guarda_amigavel_perseguir` — este
último reproduz o bug ponta a ponta: seta o alvo pelo MESMO caminho de
`AUTO_ATTACK`, roda `_process_player_attacks` 10x de verdade, confirma
`AIControlled.state` do guarda continua `IDLE` e `target_entity_id` do
player volta pra `-1`). Suíte completa 143/143 (141 + 2), sem regressão.

**Validado em jogo real pelo usuário** (15/07/2026): confirmou os 2 bugs
resolvidos (nenhum golpe/perseguição). Observação do usuário — clique
direito no guarda ainda "seleciona/persegue" visualmente como um mob
comum: **não é bug** — seleção via clique usa só `CombatStats`
(qualquer combatente), a proteção real é só no combate de fato (dano/
aggro), que já está bloqueado.

---

### 34.7 Fase 5: combate multi-tipo faccionado — `_select_target()` deixa de assumir só player (15/07/2026)

Última fase do Sistema de Facções, a mais arriscada por design (mexe no
núcleo do `EnemyAISystem`, usado por TODO mob do jogo, todo tick).
Objetivo: permitir combate de verdade entre não-jogadores (NPC-vs-NPC,
mob-vs-NPC) — o pedido original do usuário ("haverá situações em que
NPCs se atacarão entre si").

- **`_select_target()`** (`engine/world_systems.py:1436`): além do loop
  de `PlayerControlled` de sempre (inalterado), ganha um segundo loop
  sobre outros combatentes cuja relação com o mob não seja `amigavel`
  (`can_engage()`). Documentação da função também deixou de dizer
  "player" — a lógica interna JÁ era genérica (usa `CombatStats`/
  `TileMovement`/`Position`/`CombatState`, nomeados `player_*` só por
  motivo histórico), só a QUERY é que era restrita.
- **Sticky target por dano** (linha ~1581, `aggroed_by_damage`): validação
  exigia `PlayerControlled` no alvo persistido — generalizada pra aceitar
  `PlayerControlled` OU `Combatant` (sem isso, um NPC/mob agroado por
  dano de outro NPC/mob perdia o alvo fixo no tick seguinte).
- **Bloco de aggro-por-dano em `CombatSystem.deal_damage()`** (linha
  ~751): removida a restrição `attacker_is_player` — qualquer atacante
  válido (`can_engage()` já filtra `amigavel`) força o alvo a perseguir,
  não só quando o atacante é o player.
- **Regressão de performance real, pega e corrigida ainda nesta fase**:
  a primeira versão fazia CADA mob reconsultar `get_entities_with(
  Combatant, ...)` do zero dentro do próprio `_select_target()` — como
  TODO mob também é `Combatant`, isso é O(mobs²) por tick. Suíte completa
  foi de ~50s pra ~150s. Fix: 2 caches computados 1x por tick em
  `update()` (`_npc_combatants_cache`, pool pequeno — usado quando quem
  procura NÃO é NPC; `_all_combatants_cache`, pool maior — usado só
  quando quem procura É um NPC, e NPCs são sempre poucos por mapa).
  Resultado: O(mobs×npcs) em vez de O(mobs²), suíte de volta a ~55s.
- **Regressão funcional real, pega pela suíte (não visual)**: com o
  "Guarda Real" de teste agora um candidato válido de `_select_target`,
  1 teste pré-existente (`test_mob_combat_result_sent_to_both_players`)
  quebrou — o teste teleportava um mob só mexendo em `TileMovement.
  current_tile_x/y`, sem sincronizar `Position` (pixel), deixando o mob
  "fisicamente" ainda na zona de spawn real dele; a escolha de alvo por
  distância em pixel virou uma quase-empate entre o player (intenção do
  teste) e o guarda (~26px mais perto por coincidência de tile). Fix:
  teste passou a usar `tests/helpers.py::set_entity_tile()` (já existe
  pra isso, sincroniza os dois) em vez de mexer só em `TileMovement`.
- **Escopo desta entrega**: cobre o caso mais comum e já testável de
  verdade — combate mútuo por PROXIMIDADE (ambos hostis entre si, IA de
  cada lado se ataca independente, mesmo mecanismo já generalizado nas
  Fases 2/5a). O aggro-por-dano à distância (ex: skill/projétil de um
  NPC provocando um mob fora do raio de detecção) e o assist/co-aggro
  entre aliados da mesma facção (mencionados no plano original) **ainda
  não foram implementados** — ver plano em
  `C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`,
  seguem como trabalho futuro, a confirmar com o usuário se/quando
  entrar.

**Validado**: `tests/test_faction.py` ganhou `TestMultiTargetCombat` (4
testes, 29 no arquivo): mob hostil escolhe NPC de combate como alvo
(mesmo com player mais longe), NPC de combate escolhe mob hostil como
alvo (prova o lado `_all_combatants_cache`), combate mútuo completo
(200 ticks — os dois lados perdem HP um do outro, sem player envolvido),
regressão — mob neutro ainda ignora NPC amigável por proximidade. Suíte
completa 147/147 (143 + 4), sem regressão, rodada múltiplas vezes com
timing conferido (~55s, sem o regresso de performance).

**Não validado (na época)**: sessão manual em jogo real — ver §34.8, o
usuário pediu conteúdo hostil real de teste (Bandido) logo em seguida.

---

### 34.8 Bandido de teste + correção real: bystander neutro "roubava a vaga" do alvo hostil (15/07/2026)

Usuário pediu um NPC/mob hostil de teste perto do "Guarda Real" pra ver
o combate NPC-vs-NPC ao vivo — "Bandido" (`bandidos`, hostil ao guarda
e ao player) adicionado em `maps/map_1_entities.json::combat_npcs`.

**Bug real encontrado ao validar o conteúdo**: guarda e bandido nunca
engajavam, mesmo dentro do raio de aggro. Causa raiz:
`_select_target()`'s segundo loop (Fase 5, §34.7) filtrava candidatos
por `can_engage()` — que só exclui `amigavel`. Um bystander SEM facção
(ex: boneco de treino — `create_training_dummy()` não anexa `Faction`,
resolve pro sentinela "sem facção", que cai no `DEFAULT_RELATIONSHIP`
"neutro") passa em `can_engage` mas nunca deveria "vencer" a seleção de
alvo por estar mais perto — ele nunca vai ser atacado de verdade
(`is_hostile` é `False` pra ele), só ocupava a vaga do candidato hostil
real (o bandido), que ficava sempre mais longe. Fix: o loop passou a
filtrar por `is_hostile()`, não `can_engage()` — a diferença entre os
dois é exatamente essa: `can_engage` responde "posso causar dano nisso"
(usado no gate de dano), `is_hostile` responde "eu ATACO isso por
iniciativa própria" (o que `_select_target` precisa pra escolher um
alvo de proximidade). Aggro por dano (mob neutro atacado) continua
funcionando via o "sticky target" separado, não afetado.

**Regressão em teste pré-existente, descoberta e corrigida durante a
validação**: `tests/test_ranged_mob.py` ficou flaky (confirmado: 10/10
verde ANTES de qualquer mudança da Fase 5 em `_select_target`; ~10-50%
de falha depois, mesmo sem nenhuma relação de facção no cenário do
teste). Causas reais, em camadas:
1. O teste reaproveitava "o primeiro mob ranged encontrado em `_mob_eids`"
   — podia ser um mob REAL de uma zona distante, teleportado pro tile de
   teste via `set_entity_tile()` sem realinhar `InitialPosition` — mesma
   classe de bug documentada em `teleport_mob_to_player()`. Generalizar
   `_select_target()` tornou o timing por tick sensível o bastante pra
   expor essa fragilidade com mais frequência.
2. O local do teste (130,374) coincide com o CENTRO de uma zona real de
   Zumbi (raio 12) — um zumbi real vagando entre o mob de teste e o
   player bloqueava a linha de visão do tiro intermitentemente.
3. Acerto do mob (`mob_definitions.py`) pode ser <100%, somando mais uma
   fonte de variância.

Fix: teste reescrito pra sempre criar um mob sintético (nunca reaproveita
conteúdo real), em coordenadas confirmadas caminháveis e longe de
qualquer `SpawnZone` real (scan de `Tilemap.tile_matrix` antes de
escolher — uma tentativa inicial usando `(10,10)` sem checar acabou
sendo terreno 100% sólido, falhando 15/15; lição: nunca escolher
coordenada de teste "de olho"), `acerto=100` fixo (remove RNG), e
**`MapLocation` explícito no mob sintético** — sem isso, `EnemyAISystem`
(que filtra todo mob por mapa) simplesmente ignorava a entidade por
completo (nunca processada, nunca ataca); o teste antigo raramente caía
nesse caminho porque quase sempre reaproveitava um mob real (que já
vinha com `MapLocation` da própria zona de spawn).

**Validado**: suíte completa 148/148 (147 + 1: novo teste de regressão
`test_bystander_neutro_mais_perto_nao_rouba_a_vaga_do_alvo_hostil_mais_longe`,
que chama `_select_target()` diretamente em vez de checar estado
persistido — ver comentário no teste sobre por que checar
`AIControlled.target_eid` após N ticks é nulo pra alvos fora do raio de
aggro, mesmo raiz do bug do bystander). `tests/test_ranged_mob.py`
confirmado estável em 25 execuções seguidas (0 falhas). Suíte completa
rodada 5x seguidas (0 falhas).

**Não validado (na época)**: sessão manual em jogo real — o usuário
testou e relatou 3 sintomas reais, ver §34.9.

---

### 34.9 Aquisição vs Retenção de alvo (modelo WoW/LoL) + broadcast de combate mob-vs-mob (16/07/2026)

Usuário testou o Guarda vs Bandido ao vivo e relatou 3 sintomas: (1)
"ficaram frente a frente mas nenhum deles desferiu dano"; (2) ao ajudar a
matar o bandido, "o guarda ficou me perseguindo, como se estivesse em
combate comigo"; (3) o bandido "acabou morrendo" rápido demais com um
clique. Pediu explicitamente pesquisa nas mecânicas do WoW e dos minions
do LoL antes de arquitetar a solução.

**Pesquisa (fontes)**: no WoW, cada NPC tem uma *threat table* — a
AQUISIÇÃO por proximidade (aggro radius) só se aplica a unidades hostis
("red"), mas a RETENÇÃO independe de disposição: quem entrou na tabela
(ex: por dano) continua alvo até morrer/sair de alcance, e quando a
tabela esvazia o NPC "evade" (reseta e volta pra casa) — ver
[Threat (Warcraft Wiki)](https://warcraft.wiki.gg/wiki/Threat) e
[Aggro table (onlyfarms)](https://onlyfarms.gg/wiki/world-of-warcraft/aggro-table).
Nos minions do LoL, aquisição só considera unidades do TIME INIMIGO
(nunca aliados/neutros), com lista de prioridade e troca de alvo só por
prioridade maior — ver
[Minion (LoL Wiki)](https://wiki.leagueoflegends.com/en-us/Minion) e a
[documentação de IA de minions da Riot](https://boards.na.leagueoflegends.com/en/c/developer-corner/qRHotV9k-minion-ai-rules-documentation?show=rundown).
O código violava exatamente essa separação: aquisição sem filtro de
facção, e retenção implementada por acidente EM CIMA da aquisição.

**Causas raiz e fixes (3):**

1. **Sintoma "nenhum dano" → dano invisível**: o combate ACONTECIA
   inteiro server-side (HP caindo confirmado por smoke test), mas
   `COMBAT_RESULT` de mob só nasce do snapshot de HP de PLAYERS
   (`combat_processor.py`) — dano mob→mob não gerava evento nenhum;
   cliente via os dois parados com HP cheio. E por isso o bandido
   "morria instantâneo" pro player: já estava quase morto no servidor.
   Fix: `WorldServer._log_mob_damage_hit` (hook `on_damage_dealt`, já
   chamado em todo hit via `apply_damage_core`) agora, quando atacante E
   alvo estão em `_mob_eids`, appenda um COMBAT_RESULT (`hp_after`
   incluso) em `_pending_mob_attacks` — mesmo canal do mob→player, o
   cliente já sabia renderizar (atualiza `RemoteEntityMeta.hp` + FLT).
   Outcome sempre `"hit"` (o hook roda depois da escrita de HP e não
   conhece crit/block — suficiente pra sync visual; miss nem chega ali).

2. **Sintoma "guarda me perseguiu" → aquisição sem filtro de facção**:
   o loop de PLAYERS em `_select_target()` nunca teve filtro (legado de
   "todo mob é hostil ao player") — quando o bandido morria, o guarda
   ainda em estado de combate recebia o player mais próximo como "alvo
   válido" dali (a grace de 600ms nunca expirava) e seguia perseguindo,
   inclusive marcando o player como `in_combat` (bloqueia regen — "como
   se estivesse em combate comigo", literal). Fix: aquisição de players
   agora exige `is_hostile()`, igual o loop de combatentes já fazia
   (§34.8) — modelo WoW/LoL: proximidade só adquire alvo hostil.

3. **Retenção explícita (o que impedia o fix 2 antes)**: a revidada do
   mob NEUTRO (lobo atacado) dependia POR ACIDENTE do loop sem filtro —
   quando `aggroed_by_damage` é limpo na aproximação (transição
   documentada no próprio código), era a aquisição irrestrita que
   mantinha o player como alvo. Fix: o bloco de "sticky target" virou
   RETENÇÃO de verdade — mantém o alvo engajado enquanto o mob está em
   estado de combate (`AGGRO_DELAY/CHASING/ATTACKING/KITING/
   BLOCKED_BY_PLAYER`) OU `aggroed_by_damage`, validando só
   vivo+visível+`can_engage` (nunca hostilidade — retenção independe de
   disposição, igual WoW). Alvo morre → retenção falha → aquisição
   (filtrada) não acha ninguém → grace → RETURNING → IDLE — o
   equivalente exato do "threat table vazia → evade/reset".

- **Bônus (mesma classe)**: `EnemyAbilitySystem` tinha fallback de
  "player mais próximo" pra habilidades quando `target_eid` não era
  player — um mob brigando com um NPC atiraria poison/etc num player
  bystander (e um guarda com habilidades miraria no próprio player que
  protege). Fix: se o alvo real é outro combatente, NÃO dispara em
  player nenhum (habilidade contra alvo não-player fica como trabalho
  futuro — auto-attack já cobre mob-vs-mob); fallback filtrado por
  `is_hostile`.

**Comportamento emergente correto confirmado**: no smoke test do cenário
real, após matar o bandido o guarda adquiriu um ZUMBI (hostil a
`guardas_vila`) que tinha se aproximado perseguindo o player — guarda
defendendo o player de monstros, exatamente o que a matriz de facções
promete, sem uma linha de código específica pra isso.

**Validado**: 3 testes novos (`TestAcquisitionVsRetention`): guarda com
player ADJACENTE nunca alveja o player após a morte do bandido e reseta
(reproduz o sintoma 2 ponta a ponta); lobo neutro engajado mantém
retaliação com `aggroed_by_damage=False` (guarda de regressão do fix 2);
hits mob-vs-mob aparecem no delta de combate com `hp_after` (sintoma 1).
Suíte completa 151/151 (148+3), rodada 3x. Smoke test do cenário exato
relatado (player no spawn real, guarda+bandido de conteúdo real): 10
COMBAT_RESULTs mob-vs-mob emitidos, guarda nunca alvejou o player.

**Não validado (na época)**: sessão manual — o usuário testou e achou o
refinamento seguinte (§34.10).

---

### 34.10 Raio de aquisição + retaliação explícita ("dano põe na threat table") (16/07/2026)

Usuário testou §34.9 ao vivo: a briga funcionou, mas "após matar o
bandido, o guarda saiu atacando outros alvos" (zumbis longe) — pediu um
raio de aquisição, "ou quem o ataca, assim como funciona com os mobs".
Era a metade que faltava do modelo WoW: §34.9 filtrou QUEM pode ser
adquirido (hostilidade), mas não A QUE DISTÂNCIA.

**Causa raiz 1 — handoff pós-morte sem limite de distância**: o cap de 5
tiles do aggro por proximidade só existia no gate `IDLE→AGGRO_DELAY`
(local `_aggro_range_px`). Um mob já em estado de combate cujo alvo
morre recebia da aquisição o próximo hostil a QUALQUER distância (até o
leash de 20 tiles) e emendava caçada em caçada — o guarda limpava a zona
de zumbis inteira. Fix: `AGGRO_RADIUS_TILES = 5` virou constante de
classe (fonte única — o gate usa a mesma), e `_select_target()`
inicializa `best_dist` com o raio (+0.01 pra preservar a semântica
inclusiva do gate antigo em distâncias cravadas) — candidato fora do
raio nunca vence, nos DOIS loops (players e combatentes). Alvo morre +
ninguém hostil dentro de 5 tiles → reset e volta pra casa ("threat
table vazia → evade", igual §34.9 prometia). RETENÇÃO do alvo já
engajado continua ilimitada (perseguição além do raio é normal — só o
leash limita), e aggro por DANO também (revidada a qualquer distância).

**Causa raiz 2 — "quem o ataca" não funcionava de verdade (gap real
descoberto implementando o raio)**: os blocos de aggro por dano
(melee em `CombatSystem.deal_damage`, mágico em
`spell_completion_processor`/`ui/spell_system`) setavam
estado/`aggroed_by_damage` mas NUNCA `target_eid` — o alvo da revidada
vinha POR ACIDENTE do loop de aquisição sem filtro (removido em §34.9).
Ou seja: desde §34.9, um mob NEUTRO atacado em melee entrava em
AGGRO_DELAY por 600ms e desistia sem nunca golpear de volta (a aquisição
filtrada não lhe dava alvo nenhum) — regressão silenciosa que nenhum
teste pegava (o teste da Fase 2 só checava `state != IDLE` logo após o
hit, não a briga sustentada). Fix: os 3 blocos setam
`target_eid = attacker_id` — semântica de threat table: dano põe o
atacante na tabela; a RETENÇÃO (§34.9) faz o resto. O bloco ranged
(`_server_apply_ranged_physical`) já setava desde sempre.

**Regressão em 2 testes pré-existentes, mesma classe já vista**:
`test_mob_despawn_sent_to_both_players` e
`test_player_corpse_stays_dead_until_revive` teleportavam mob só por
tile (Position/pixel ficava na zona original) — com a aquisição agora
limitada por distância EM PIXEL, o mob "fisicamente longe" não achava
alvo, entrava em RETURNING (evasão = imune) e não morria/não atacava.
Fix: os 2 passaram a usar `tests/helpers.py::teleport_mob_to_player()`
(tile+pixel+InitialPosition), que existe exatamente pra isso. Terceira
ocorrência dessa fragilidade (após `test_mob_combat_result...` e
`test_ranged_mob`) — TODO teste que mover mob deve usar os helpers,
nunca escrever `current_tile_x/y` na mão (mesma regra que o código de
produção já tem com `snap_to_tile`).

**Validado**: 2 testes novos —
`test_apos_matar_o_alvo_nao_adquire_hostil_fora_do_raio_de_aggro`
(reproduz o relato: hostil a 10 tiles NÃO é adquirido no handoff, guarda
reseta) e `test_mob_neutro_revida_ataque_melee_ponta_a_ponta` (lobo
neutro atacado via `_process_player_attacks` real ganha o atacante como
alvo e CONTINUA brigando após a grace — pega a regressão silenciosa da
causa 2). Teste do bystander ajustado (bandido pra dentro do raio — a
intenção dele é hostilidade, não distância). Suíte completa 153/153
(151+2), rodada 3x. Smoke do cenário real: 10 COMBAT_RESULTs mob-vs-mob,
e após a morte do bandido o guarda fica `target=-1, IDLE` em casa.

**Não validado**: sessão manual em jogo real.

---

### 34.11 Prontidão pra MOBA (times) + contexto PvP plugável + regressão PvP corrigida (16/07/2026)

Usuário explicou o objetivo de longo prazo: campos de batalha estilo
MOBA com 2 times — NPCs do time A hostis a NPCs E players do time B, e
vice-versa — e perguntou se a arquitetura atual cobre. Resposta
(verificada por script, não por suposição):

**Já coberto por design:**
- Times = facções na matriz (`"time_a"`/`"time_b"` hostis = 2 linhas em
  `faction_data.py`). NPC-vs-NPC de times é exatamente o Guarda vs
  Bandido já validado.
- Player pode ter time HOJE: `get_entity_faction()` é
  componente-primeiro — `Faction` anexado num player SOBRESCREVE o
  default `"jogadores"` (provado por script: `time_a` vs `time_b` →
  `can_engage` True, dano flui). Minion adquirindo player inimigo usa o
  mesmo caminho de aquisição já existente (agnóstico a player/NPC).
- Fogo amigo intra-time bloqueado de graça (mesma facção = amigável).

**Lacunas mapeadas (futuras):** fluxo de atribuição de time (matchmaking
/entrada no campo anexa a Faction), facção de player no protocolo
(`ENTITY_SPAWN` de player não carrega facção → nameplate de player
inimigo não fica vermelho ainda), torres/estruturas (combatente
estacionário), waves com pathing de lane, assist/co-aggro, instância de
mapa (`ENTER_INSTANCE` 🔲).

**Regressão real descoberta na verificação**: o PvP de mundo aberto
(flag global `pvp_enabled`) estava silenciosamente MORTO desde o gate de
facção amigável (§34.4) — dois players = mesma facção `"jogadores"` =
amigável = dano 0. Nenhum teste de dano PvP ponta a ponta existia pra
acusar (o único teste "PvP" da suíte assertava justamente a AUSÊNCIA de
efeito colateral). Confirmado por script antes de afirmar.

**Fix — contexto PvP plugável** (decisão do usuário: PvP vai existir em
situações diferentes — duelo por convite, arenas, zonas, campos de
batalha — ou seja, PvP é CONTEXTUAL, não regra fixa de facção; mesmo
modelo do WoW, onde duelo/war mode/arena são camadas de permissão sobre
as facções):
- `engine/faction_system.py::register_pvp_context(resolver)` — o gate de
  facção continua o padrão (amigável = bloqueado); entre DOIS PLAYERS
  (nunca mob/NPC), um contexto registrado pode liberar. Ponto único onde
  duelo/arena/zona vão plugar.
- Primeira implementação (registrada em `WorldServer._load_all_maps`): a
  flag global `pvp_enabled` de sempre — mas só válida pro caso SEM TIME
  (ambos na facção default `"jogadores"`). Restaura o PvP de mundo
  aberto exatamente como era, e players com Faction de time (futuro
  MOBA) ficam sob regra de facção pura: inter-times briga sem depender
  de contexto, intra-time protegido de fogo amigo mesmo com a flag
  global ligada.

**Validado**: `TestPvpContext` (5 testes novos): PvP mundo aberto
funciona com a flag ligada (a regressão), bloqueado com flag desligada,
times distintos brigam por facção pura (flag DESLIGADA de propósito —
prova que não depende do contexto), mesmo time protegido de fogo amigo
com a flag LIGADA, e mob nunca ganha permissão por contexto (só
player-vs-player). Suíte completa 158/158 (153+5), rodada 3x.

**Não validado**: PvP manual em jogo real com 2 clientes.

---

### 34.12 Clique direito/SPACE em alvo amigável não inicia mais combate/perseguição (16/07/2026)

Bug relatado pelo usuário: clique direito no Guarda Real fazia o player
PERSEGUI-LO e entrar em combate. O servidor já recusava o alvo
(`set_player_target`, §34.6), mas o CLIENTE setava `is_pursuing=True` +
`enter_combat()` localmente antes de qualquer resposta — perseguição
visual + status de combate (bloqueia regen) contra um alvo que nunca
seria atacável.

Fix client-side, no gate de decisão (`ui/systems.py`):
- **Clique direito** em alvo com relação `amigavel` (`can_engage` False)
  vira SELEÇÃO PURA — mesmo comportamento do clique esquerdo (seleciona,
  não persegue, não entra em combate). O proxy local do mob/NPC remoto
  carrega a `Faction` real do servidor (`ENTITY_SPAWN.faction`, §34.2),
  então o cliente resolve a relação corretamente sozinho.
- **SPACE** (`_space_engage`): candidatos amigáveis são pulados — o
  proxy do NPC de combate carrega a tag `Enemy` no cliente (via
  `create_enemy` em `_spawn_remote_mob`), então sem o filtro o SPACE
  podia engajar o guarda se ele fosse o inimigo mais próximo.
- **Player remoto**: sem `Faction` e sem `PlayerControlled` no cliente,
  resolve pro sentinela sem-facção → `neutro` → atacável (PvP de mundo
  aberto preservado). Quando a facção de player for sincronizada
  (times/MOBA, lacuna já mapeada em §34.11), este MESMO gate passa a
  proteger aliados automaticamente — pedido do usuário ("o mesmo serve
  para o player quando está com o status friendly") coberto por design,
  pendente só da sincronização.

**Validado**: script client-shaped confirmando as 3 decisões do gate
(guarda amigável → False; mob hostil → True; player remoto → True).
Suíte completa 158/158.

**Não validado**: clique real em jogo (perseguição não iniciando, seleção
ainda funcionando).

---

### 34.13 Contextos PvP — friendly por default, modal de interação, duelo (16/07/2026)

Visão do usuário formalizada em plano aprovado (roadmap completo no
plano; Leva 1 = Fases A-C abaixo): **players são todos amigáveis por
default** — PvP só existe em contexto explícito: (1) duelo por convite
(hostis somente um ao outro); (2) facção de player inimiga; (3) zona PvP
(solo = todos hostis; em party = só quem está fora do grupo); (4) campos
de batalha/arenas por times (players E NPCs). Decisões travadas: duelo
termina estilo WoW (golpe letal → perdedor com 1 HP, ninguém morre); o
modal de interação substitui o shift+clique por completo.

**Fase A — friendly por default** (`6f4c450`): o "PvP de mundo aberto"
via flag global morreu de propósito — `pvp_enabled` virou só kill-switch
de emergência. `WorldServer._pvp_allowed_between` é o resolver COMPOSTO
registrado como contexto PvP (por ora consulta só `_duel_pairs`; zona
PvP/etc. entram como novas consultas). Os 3 consumidores do flag antigo
migraram pra `can_engage`: auto-attack PvP (`combat_processor`), inclusão
de players em alvo de AoE (`_combat_targets`, agora por par caster/alvo)
e tid de player em CAST_SKILL (`skill_processor`). Cliente:
`get_entity_faction` também resolve `RemoteControlled` como
`"jogadores"` — proxy de player remoto era sentinela sem-facção
(neutro→atacável); agora amigável, e os gates de clique direito/SPACE
(§34.12) bloqueiam de graça.

**Fase B — modal de interação + Seguir** (`1f46a48`): clique direito em
player amigável abre o modal Negociar/Duelar/Seguir (shift+clique
removido; NPC amigável continua seleção pura — modal é só pra players).
Popup generalizado em `client/trade_handlers.py` com geometria única
draw/hit-test (`_player_popup_button_rects`). "Seguir":
`PlayerAutoMove.follow_eid` + `_process_follow` (`ui/systems.py`) —
acompanha o player em movimento reutilizando o pathing de alvo móvel da
perseguição (`_auto_move_step`), parando adjacente; cancela em WASD,
perseguição de combate, clique de chão, alvo sumido.

**Fase C — duelo** (`975a945`): fluxo espelha o trade.
`server/duel_processor.py` (`DuelProcessorMixin`): `_duel_pairs`
(consultado pelo contexto PvP — par hostil somente um ao outro),
convites, tick de distância (`DUEL_MAX_DIST_TILES=20` — maior que o do
trade de propósito, a luta precisa de espaço pra kite), disconnect.
**Golpe letal**: `register_lethal_interceptor` em `engine/core_systems`
(hook plugável no ponto único de dano — engine puro, server registra,
mesmo padrão do `register_pvp_context`): o golpe que mataria deixa o
perdedor com 1 HP, encerra o duelo e enfileira `DUEL_END{win}`
(consumido pelo broadcast loop por tick — a morte acontece dentro do
tick, longe de qualquer sessão async). Cliente
(`client/duel_handlers.py`): modal de convite, `DUEL_START` registra
contexto PvP client-side (só o oponente vira atacável) + aviso,
`DUEL_END` limpa e anuncia vitória/derrota; nameplate do oponente fica
VERMELHO durante o duelo (`build_player_hud` ganhou `hp_color`, mesmo
esquema de disposição do mob).

**Pontos de atenção anotados no plano** (interferências futuras):
1. **DoT em duelo pode matar de verdade** — `StatusEffectSystem.
   _apply_tick` escreve HP direto, sem passar por `apply_damage_core`; o
   interceptor de golpe letal não cobre tick de DoT. Bug conhecido da
   Leva 1; a correção certa (rotear `_apply_tick` pelo core) é refactor
   à parte.
2. Facção de player no protocolo é pré-requisito das fases D (facções) e
   G (times) — o duelo não depende (canal próprio DUEL_START/END).
3. Party (fase E) vem antes de zonas PvP (fase F) — regra "só quem está
   fora do grupo".
4. Contextos são só player-vs-player por design — NPC nunca ganha
   permissão por contexto.

**Validado**: `tests/test_duel.py` (9 testes — lifecycle, golpe
letal→1HP+DUEL_END+hostilidade encerrada, decline, distância, logout,
terceiro player protegido durante duelo, NPC amigável inatacável,
kill-switch) + `TestPvpContext` reescrito (7 — semântica invertida).
Suíte completa 169/169, rodada 3x.

**Validado em jogo real pelo usuário** (16/07/2026) — com 1 correção,
ver §34.14.

---

### 34.14 Correção real: durante o duelo, clique direito no oponente abria o modal em vez de atacar (16/07/2026)

Usuário validou a Leva 1 com 2 clientes e achou o único problema: após
aceitar o duelo, clique direito no oponente ainda abria o modal de
interação (Negociar/Duelar/Seguir) — deveria INICIAR O COMBATE, igual
mob.

Causa raiz: o gate do contexto PvP em
`engine/faction_system.py::can_engage` exigia `PlayerControlled` dos
DOIS lados antes de consultar o resolver — correto no SERVIDOR (todo
player é `PlayerControlled` lá), mas no CLIENTE o oponente é um proxy
`RemoteControlled`. O contexto de duelo registrado pelo `DUEL_START`
(client/duel_handlers.py) nunca era consultado → `can_engage` devolvia
`False` (amigável) → o clique direito caía no ramo do modal. O
DANO/perseguição funcionavam porque o servidor (autoritativo) liberava —
só a UX do clique estava presa no ramo errado.

Fix: helper `_is_player_entity()` — "player" pro gate de contexto é
`PlayerControlled` OU `RemoteControlled` (consistente com
`get_entity_faction`, que já tratava os dois como `"jogadores"` desde a
Fase A). Servidor inalterado.

**Validado**: `TestDuelClientSide` (3 testes novos, mundo com formato de
cliente — local `PlayerControlled` + proxy `RemoteControlled` + contexto
espelhando o `_duel_ctx` real): oponente em duelo vira engajável
(clique direito → ataque), terceiro proxy remoto continua amigável
(modal), e sem contexto volta ao modal (pós-DUEL_END). Suíte completa
172/172, rodada 3x.

**Não validado**: novo teste manual com 2 clientes (clique direito no
oponente atacando direto durante o duelo).

### 34.15 Mensagens de fim de duelo (16/07/2026, revisado no mesmo dia)

Pedido do usuário após validar §34.14: no golpe letal, além do
`WARN` de vitória/derrota, um anúncio deve aparecer no chat.

Primeira tentativa (client-side, aba Combate) foi corrigida pelo usuário
no ato: ele queria a aba **Local** — visível pra QUALQUER UM na área, não
só os dois duelistas. Como a aba Local é alimentada por `CHAT_MESSAGE`
via broadcast **AOI do servidor** (não dá pra montar client-side sem
round-trip, e cada cliente só saberia dos próprios vizinhos via AOI de
qualquer forma), o anúncio virou uma responsabilidade do servidor:

- `server/duel_processor.py::end_duel` — quando `reason="win"`, captura
  `tx`/`ty` (tile do vencedor no momento do golpe, via `TileMovement`) e
  `map` (via `get_entity_map(winner_eid)`) no evento enfileirado.
- `server/session.py` (broadcast loop, junto do `consume_duel_end_events`
  existente): pro caso `"win"`, resolve os nomes dos dois lados via
  sessão (mesmo padrão dos outros broadcasts) e manda `CHAT_MESSAGE
  {sender:"Sistema", text:"{vencedor} venceu {perdedor} em um duelo!",
  channel:"local", color:[255,200,80]}` via
  `_sessions_in_aoi(tx, ty, map)` — regra do projeto (CLAUDE.md): nunca
  iterar `_sessions` com distância à mão pra broadcast direto novo.
- Cliente: `client/duel_handlers.py::_handle_msg_duel_end` manteve só o
  `WARN` de tela ("Você venceu o duelo" / "Você foi derrotado") — o
  anúncio de chat não é mais responsabilidade dele, o `CHAT_MESSAGE`
  chega pelo pipeline normal (`_handle_msg_chat_message`) e cai na aba
  Local sozinho.

**Validado**: `TestDuelLifecycle::test_golpe_letal_deixa_perdedor_com_1hp_e_encerra`
ganhou assert de `tx`/`ty`/`map` no evento. Suíte completa 172/172,
rodada 3x.

**Não validado**: sessão manual com 2 clientes + um terceiro observador
parado perto (deve ver o anúncio na aba Local sem ter duelado).

### 34.16 AoE (Nova Congelante) enraizava/danificava NPC amigável (16/07/2026)

Usuário relatou: usar Nova Congelante enraizou o "Guarda Real" (NPC de
combate amigável) também — precisava investigar dano em área acertando
alvos amigáveis.

Causa raiz: `server/world_server.py::_combat_targets()` — o ponto único
de alvos válidos pra qualquer skill/spell de player (AoE, snapshot de
HP/efeitos, etc.) — incluía `set(self._mob_eids)` **sem nenhum filtro de
hostilidade**. `_mob_eids` guarda QUALQUER `Combatant` (gate é
`Combatant`, não `Enemy` — decisão da Fase 4 do Sistema de Facções,
propositalmente inclusiva pra cobrir NPC de combate amigável tipo
guarda), então "Guarda Real" sempre esteve ali junto dos mobs hostis. Só
PLAYERS passavam por `can_engage()` nessa função (herança da Fase A —
antes disso "amigável" só existia entre players); mob/NPC nunca passou
pelo mesmo crivo. `_server_nova_congelante` (`server/
spell_completion_processor.py`) itera `_combat_targets()` e aplica dano
+ `apply_effect(..., "root", ...)` incondicionalmente pra cada entidade
retornada — o dano em si teria sido bloqueado por `apply_damage_core`
(gate `blocked_friendly`), mas o **root nunca passava por lá** (efeito
de status é aplicado direto, fora do choke point de dano), então o
guarda enraizava mesmo com 0 de dano.

Fix: `_combat_targets()` agora aplica `can_engage(exclude_eid, alvo)`
tanto pra mobs/NPCs quanto pra players — mesmo crivo, uma função só.
`exclude_eid` é sempre o CASTER (todo chamador de `_combat_targets` é
uma skill/spell de player), então a relação por par já resolve certo:
mob hostil/neutro → `can_engage` True (comportamento inalterado); NPC
amigável → False (excluído, como devia ser desde sempre); par de
duelo/contexto PvP → inalterado (já usava esse crivo). Como é o ÚNICO
ponto de alvos de AoE no servidor, a correção cobre Nova Congelante E
qualquer outra skill em área atual/futura de graça — não precisou
mexer em nenhum handler de skill individual.

**Validado**: `tests/test_faction.py::TestCombatNpcArchetype` ganhou 2
testes — `test_combat_targets_exclui_npc_amigavel_de_aoe` (unitário,
direto na função) e `test_nova_congelante_nao_enraiza_npc_amigavel`
(ponta a ponta pelo handler real: sem dano, sem root). Suíte completa
174/174, rodada 3x.

**Nota sobre flakiness observada durante a investigação**: rodar a
suíte completa junto com este fix expôs uma instabilidade JÁ EXISTENTE
e não relacionada em `tests/test_server.py`/`tests/test_session.py`
(`first_mob()` escolhe "o primeiro mob hostil" de `_mob_eids` — depende
de qual mob o `SpawnZoneSystem` sorteou durante os ticks de `setUp`, e
alguns testes de auto-attack dependem de rolagens de acerto reais
dentro de uma janela curta de ticks; nada disso usa `_combat_targets`
nem foi tocado aqui). Confirmado por comparação: HEAD limpo (sem este
fix) — 5/5 rodadas OK; com o fix — 3/3 rodadas OK depois de ajustar os
2 testes novos pra não chamar `run_ticks` (evitando consumir do
`random` global à toa); as falhas esporádicas vistas no meio do caminho
apareceram em testes que não tocam AoE/facção, confirmando que são
pré-existentes. Não é uma regressão deste fix, mas fica registrado como
ponto de atenção pra quem mexer em `first_mob()`/spawn de mobs no
futuro — considerar seedar `random` por teste ou tornar `first_mob()`
determinístico.

### 34.17 Skill à distância mirada em NPC amigável gastava mana/cooldown à toa (16/07/2026)

Usuário relatou: arqueiro/mago usando Picada de Escorpião, Flecha
Reiterada e Bola de Fogo contra o "Guarda Real" — a flecha/bola viaja
até o NPC, mas não causa dano.

O "sem dano" em si é CORRETO (mesma proteção de `apply_damage_core`'s
`blocked_friendly` que já vale pra melee, §34.16, agora confirmada
também pra magia/à distância). O bug real é ANTES disso: `server/
skill_processor.py::_process_skill_requests` resolve o `tid` do
`CAST_SKILL` em dois branches — mob e player. O branch de PLAYER já
checava `can_engage()` (Fase A, 16/07/2026), mas o branch de MOB/NPC
aceitava QUALQUER eid em `_mob_eids` incondicionalmente, sem checar
hostilidade — e mesmo o branch de player, ao falhar, só deixava de
ATUALIZAR `combat_state.target_entity_id` (sem recusar o cast), então o
handler rodava do mesmo jeito com o alvo antigo/stale. Resultado: cast
time completo, animação de projétil, mana e cooldown gastos — tudo pra
um golpe que nunca poderia ter efeito, sem nenhum feedback ao jogador
explicando por quê.

Fix: os dois branches agora RECUSAM o `CAST_SKILL` inteiro quando
`can_engage(caster, tid)` é `False` — mesma proteção que
`set_player_target()` já dava pro auto-attack (§ anterior), só que
aplicada ANTES de qualquer mana/cooldown/cast time ser gasto. Recusa
envia `SKILL_RESULT{failed:True, reason:"Alvo amigável"}` — o cliente já
tinha o pipeline pra isso (`client/network_handlers.py` mostra
`payload.reason` como `WARN` automaticamente, mesmo padrão usado por
`is_skill_authorized`).

**Validado**: `tests/test_faction.py::TestCombatNpcArchetype::
test_cast_skill_recusa_alvo_amigavel_sem_gastar_recurso` — Picada de
Escorpião contra Guarda Real: HP intacto, mana intacta, cooldown NÃO
registrado, `SKILL_RESULT` com `failed=True`. Suíte completa 175/175.

**Validado em jogo real pelo usuário** (17/07/2026) — com 1 efeito
colateral do lado cliente, ver §34.18.

**Investigado e NÃO confirmado como bug** (mesmo relato do usuário): mago
vencedor de duelo contra arqueiro observado "estranhamente devagar" logo
após o golpe letal. Não foi encontrado nenhum código no caminho de fim
de duelo (`server/duel_processor.py::end_duel`/`_duel_lethal_interceptor`,
`engine/core_systems.py::apply_damage_core`) que toque velocidade,
`StatusEffects` ou `TileMovement.slow_mult` do VENCEDOR — só o perdedor
tem "polymorph"/"sleep" removidos ali, e só quando dano > 0. Hipótese
mais provável: "slow" de 30% aplicado por Picada de Escorpião (arqueiro,
3s de duração, reaplicado a cada acerto) ainda ativo no mago no momento
exato da vitória — expira sozinho, sem relação com o fim do duelo.
Achado colateral digno de registro: `StatusEffectSystem.update()`
(`engine/core_systems.py`) pula a entidade inteira (`if not sfx.effects:
continue`) quando `StatusEffects.effects` está vazio — qualquer código
que chame `.effects.clear()` diretamente (fora do loop de expiração
normal) deixa `TileMovement.slow_mult`/`CombatState.is_rooted` PARADOS
no valor antigo até a entidade ganhar um efeito novo, já que o bloco de
sincronização nunca roda de novo enquanto `effects` continuar vazio.
Confirmado em 3 call sites de MOB (`engine/world_systems.py:1796/2368/
2433`, reset de leash/RETURNING→IDLE) que fazem `.clear()` sem resetar
`slow_mult` — não explica o relato do mago (esses 3 são só de mob), mas
é uma classe de bug real pra investigar se o padrão aparecer de novo.
Pendente: usuário confirmar se a lentidão persistiu além de ~3-5s (slow
natural expirando) ou pareceu permanente (nesse caso, revisitar).

### 34.18 Arqueiro travado perseguindo o Guarda Real após CAST_SKILL ser recusado (17/07/2026)

Efeito colateral do fix §34.17: usuário testou o arqueiro mirando o
Guarda Real de novo — o arco "tensiona" (som de `cast_start` toca), mas
o tiro não sai, E o personagem entra em combate e volta pra posição de
range sempre que tenta andar pra outro lado.

Causa raiz: `ui/systems.py::_use_skill_visual_only` (caminho ONLINE de
uso de skill) seta `combat_state.is_pursuing = True` **incondicionalmente
pra qualquer skill ofensiva, ANTES do range check** — mesmo com
`cast_time` (comentário original: "Garante que pressionar skill inicia o
chase/auto-attack mesmo fora de alcance"). Isso é client-side prediction
sem qualquer noção de facção: o alvo resolvido (`combat_state.
target_entity_id`, já selecionado manualmente antes de apertar a skill)
nunca era checado contra `can_engage()` no cliente.

Antes do fix §34.17, o servidor ACEITAVA o cast (bug antigo) e a
conclusão normal do cast eventualmente limpava o estado client-side —
o "preso pra sempre" não acontecia, só o "sem dano" silencioso. Com o
CAST_SKILL agora sendo recusado na origem (`failed:True`), o round-trip
de conclusão normal nunca chega, e **nada no cliente limpava
`is_pursuing` numa falha** (só num cast bem-sucedido) — o personagem
fica perseguindo o Guarda Real pra sempre.

Fix (2 camadas, mesmo padrão "recusar antes de fingir que funcionou" do
§34.17):
1. **Gate primário** — `ui/systems.py::_use_skill_visual_only`: logo após
   resolver `_target_local` (explícito ou auto-selecionado), checa
   `can_engage(player, alvo)` — se `False`, `WARN.add("Alvo amigável")` e
   `return False` ANTES de tocar som/`enter_combat`/`is_pursuing`. Mesmo
   `can_engage` já usado no gate de clique direito (§34.12) — o
   componente `Faction` real do NPC já chega ao cliente via
   `ENTITY_SPAWN`/`AOI_UPDATE` (`client/remote_entity_handlers.py:707`).
2. **Rede de segurança** — `client/network_handlers.py::
   _handle_msg_skill_result`, branch `failed=True`: quando
   `reason == "Alvo amigável"`, limpa `combat_state.is_pursuing = False`
   também (cobre qualquer caso que escape do gate #1, ex.: alvo virou
   amigável DEPOIS do clique mas antes do servidor responder).

**Validado**: suíte completa 175/175, rodada 3x (mudança é só
client-side/UX — sem novo teste automatizado dedicado, coberto
indiretamente pela suíte de regressão do servidor que já valida a
recusa em si).

**Não validado**: sessão manual com arqueiro/mago mirando o Guarda Real
(deveria recusar a skill NA HORA — sem som, sem entrar em combate, sem
perseguição).

### 34.19 Party/Grupo + XP compartilhado (Fase E do roadmap, 17/07/2026)

Próximo item do roadmap combinado com o usuário depois da Leva 1 (PvP
por contexto + duelo): **Fase E — Party/Grupo**, pré-requisito da Fase F
(Zonas PvP: "sozinho = todos hostis, em grupo = só quem tá fora").
Decisões do usuário: convite pelas DUAS vias (modal **e** comando de
chat `/convidar Nome`, estilo WoW), tamanho máximo 5, escopo com XP
compartilhado incluído.

**Achado-chave da exploração**: XP **já** era dividida
proporcionalmente por dano entre múltiplos atacantes
(`server/server_death_handler.py`, `_mob_damage_log`) — não era "só
quem mata leva tudo". O que faltava pro grupo: quem NÃO bateu no mob
(mas está no grupo e por perto) também ganhar uma fatia.

**Decisões de design** (engenharia, não perguntadas ao usuário):
- Qualquer membro do grupo pode convidar (não só o líder) — só
  **expulsar** exige ser líder.
- **Sem checagem de distância** pra convidar OU permanecer agrupado —
  diferente de trade/duelo, replica o `/invite` de WoW (funciona no
  mapa inteiro). A única restrição de fato é o comando de chat só
  resolver nomes de players **visíveis** (`RemoteControlled` local,
  sem lookup de nome global no servidor).
- **XP compartilhado**: fatia por proporção de dano continua entre
  atacantes SEM grupo em comum. Entre membros do MESMO grupo, a soma
  das fatias que esse grupo ganharia forma um "pool", redistribuído
  IGUALMENTE entre os membros do grupo dentro de
  `PARTY_XP_SHARE_RADIUS_TILES` (reusa `AOI_RADIUS`) da morte —
  incluindo quem não bateu. Fora do raio, não ganha nada dessa morte.
  Loot/quest-kill continuam "primeiro atacante" (inalterado) — só XP é
  compartilhada nesta leva.
- **HP no frame de grupo**: só atualiza em tempo real pra membros
  visíveis no AOI do próprio cliente (igual qualquer player remoto já
  funciona). Membro fora de alcance mostra o último HP do
  `PARTY_STATE`. Sincronização de HP independente de distância é
  limitação conhecida — fica pra uma leva futura.
- **Sem componente ECS novo**: estado de grupo vive em dicts no
  `WorldServer` (`_parties`, `_player_party_id` — mesmo padrão de
  `_duel_pairs`), não em componente sincronizado por entidade.

**Servidor**: `server/party_processor.py` (`PartyProcessorMixin`,
espelha `DuelProcessorMixin`, mas N-ário em vez de par fixo —
`_parties: dict[party_id, {"leader_eid","members"}]` +
`_player_party_id` como índice reverso). API: `request_party_invite`/
`respond_party_invite` (cria grupo novo OU expande o existente do
requester), `leave_party` (promove o próximo membro se o líder sai,
desfaz o grupo se sobra só 1), `kick_from_party` (só líder),
`end_parties_of` (disconnect), `get_party_snapshot` (monta o payload de
`PARTY_STATE` lendo `CharacterStats`/`CombatStats` direto — este mixin
vive no `WorldServer`, sem acesso aos objetos `Session`),
`_party_members_in_range` (chebyshev + mesmo mapa, generalização de
`_duel_in_range` pra N membros). Eventos de mudança de estado
enfileirados (`consume_party_state_events`, mesmo padrão de
`consume_duel_end_events`) e consumidos pelo broadcast loop de
`server/session.py` — cada item é um `party_id` (manda `PARTY_STATE`
pra todos os membros atuais) ou uma tupla `("left", eid)` (`PARTY_STATE`
vazio individual pra quem saiu/foi expulso).

**XP compartilhado**: `server/server_death_handler.py` — a leitura de
posição/mapa do mob foi movida pra ANTES do bloco de XP (usada pelo
split de grupo). Depois do split proporcional existente por
`damage_log`, passo novo agrupa as entradas de `pending_xp` recém-
geradas por `party_id`, soma em um pool por grupo, acha quem está no
raio (`_party_members_in_range`) e substitui as entradas originais
desse grupo por uma entrada por membro em range
(`pool // len(in_range)`, mínimo 1). Atacante sem grupo ou de outro
grupo mantém a entrada individual intocada.

**Cliente**: `client/party_handlers.py` (`PartyHandlers`, espelha
`DuelHandlers`) — modal de convite (Aceitar/Recusar), frame de grupo
simples (nome+nível+barra de HP, canto superior esquerdo, com botão
Sair/Expulsar por linha), e `_try_handle_party_chat_command()` — parsing
de `/convidar Nome` (primeira vez que o chat interpreta algo antes de
mandar como texto normal, chamado por
`client/chat_handlers.py::_send_chat_message`). `client/
trade_handlers.py::_PLAYER_POPUP_BUTTONS` ganhou um 4º botão "Convidar
p/ Grupo" (mesma geometria genérica de `_player_popup_button_rects`,
`TRADE_POPUP_H` cresceu 138→172).

**Validado**: `tests/test_party.py` (14 testes) — ciclo de vida completo
(convite/aceite/recusa/crescer grupo existente/limite de 5/sair/líder
sai promove próximo/grupo de 2 desfaz/expulsão só líder/desconexão/
convite pra alvo já agrupado) + XP compartilhado (membro que não bateu
recebe fatia igual, membro fora do raio não recebe nada, atacante de
fora do grupo mantém fatia individual intocada). Suíte completa
189/189, rodada 3x.

**Não validado**: sessão manual com 2+ clientes — convidar via modal E
via `/convidar Nome`, grupo crescendo por convite de não-líder, líder
saindo com promoção automática, expulsão recusada por não-líder, XP
compartilhado com um membro fora de alcance.

**Validado em jogo real pelo usuário** (17/07/2026) — com 2 bugs
encontrados, ver §34.20 e §34.21.

### 34.20 Nameplate de player remoto travava no level de login (17/07/2026)

Usuário relatou: level do player remoto não sincroniza — nameplate
mostra sempre o level de quando ele logou, nunca reflete level-up.

Causa raiz DUPLA: (1) `server/session.py` (payload de "player ficou
visível" no AOI) lia `Session.char_data["level"]` — um snapshot
cacheado no LOGIN, nunca atualizado durante a sessão; (2) o
`queue_stats_update` do level-up (`server/world_server.py`, consumo de
`_death_handler.consume_xp()`) é **privado** (só o dono recebe) —
ninguém que já estivesse observando o player era avisado quando ele
subia de nível de verdade.

Fix, sem novo mecanismo — reaproveitando o que já existia:
- `WorldServer._sync_player_hp_dirty()` (`server/world_server.py`) já
  detecta QUALQUER mudança de HP/max_hp de qualquer player a cada tick
  e broadcasta AOI automaticamente ("sem precisar de código por
  feature", doc original) — passou a rastrear `char.level` na mesma
  tupla de dirty-check e incluir `"level"` no payload. Level-up SEMPRE
  muda `max_hp` (ganho de vitalidade), então o dirty-check já disparava
  nesse exato momento — só faltava carregar o level junto.
- `server/session.py`: o payload de "player ficou visível" (AOI_UPDATE)
  passou a ler `CharacterStats.level` (componente VIVO) em vez de
  `char_data["level"]` (snapshot morto) — corrige quem vê o player PELA
  PRIMEIRA VEZ depois de ele já ter subido de nível.
- `client/network_handlers.py::_handle_msg_stats_update` (branch de
  player remoto): passou a aplicar `payload["level"]` em
  `RemoteControlled.level`, igual já fazia com hp/hp_max.

**Validado**: `tests/test_server.py::TestRegressionBugs` ganhou 2 testes
— level-up dispara o broadcast de HP com `level` atualizado, e uma
segunda chamada sem mudança nenhuma não reemite (dirty-check cobre a
tupla completa, não só hp/max_hp como antes). Suíte completa 195/195,
rodada 3x.

**Não validado**: sessão manual com 2 clientes — um player subir de
nível e o outro ver o nameplate atualizar em tempo real, sem precisar
sair e voltar da área de visão.

**Follow-up (mesmo dia)**: usuário testou e reportou que persistia —
"na tela do guerreiro está correto [óbvio, é o próprio HUD local, nunca
passou por rede], mas na tela do mago o level do guerreiro ainda está
1". Faltava um TERCEIRO ponto com a mesma causa raiz, o pior dos três:
`server/session.py::_spawn_and_start` (snapshot `WORLD_STATE` enviado
no LOGIN de um player, listando quem já está por perto) lia
`s2.char_data.get("level", 1)` — o snapshot de login do OUTRO player
(`s2`), não o level ao vivo. Se o mago loga DEPOIS do guerreiro já ter
subido de nível, recebe o level congelado em quando o guerreiro logou
(nesse caso, 1). E como esse mesmo bloco já marca o eid como
`known_eids`, o caminho "player ficou visível" (o fix original acima)
NUNCA re-roda pra corrigir depois — só um level-up NOVO, que aconteça
DEPOIS do mago logar, dispararia o broadcast de tick e salvaria. Fix:
mesma troca de `char_data`→componente vivo (`CharacterStats.level` de
`s2.entity_id`), agora nos 3 pontos (tick-broadcast, "ficou visível",
WORLD_STATE de login).

**Validado**: `tests/test_session.py::TestAOISubscription::
test_world_state_mostra_level_atual_nao_o_de_login` — player A sobe pra
level 10 via componente (simulando progressão real pós-login, sem
tocar `char_data`), player B loga depois e o `WORLD_STATE` de B mostra
level 10 de A, não 1. Suíte completa 196/196, rodada 3x.

**Follow-up 2 (18/07/2026)**: usuário confirmou ter reiniciado servidor
E cliente e reportou que AINDA acontecia, de forma intermitente (print
mostrando o mesmo player com level 7 numa tela e level 1 noutra).
Reaudita dos 3 pontos já corrigidos não achou regressão — causa raiz
era uma QUARTA, mais sutil: `_sync_player_hp_dirty()` tem um dedup
(`_already`) pra não mandar DOIS `STATS_UPDATE` do mesmo player no
mesmo tick quando outro sistema (`skill_processor.py`/
`spell_completion_processor.py`, dano PvP — chance real e alta durante
duelo, onde HP muda a cada golpe) JÁ enfileirou um broadcast de HP pro
mesmo player. O guard antigo (`if peid not in _already: append`)
pulava a ENTRADA INTEIRA quando havia colisão — level junto. Se o
level-up caía no MESMO tick de qualquer dano/cura do player, o level
nunca entrava em NENHUM broadcast daquele tick — e como o cache de
dirty-check já tinha sido atualizado ANTES do dedup, nenhum tick
seguinte tentava de novo (silenciosamente perdido pra sempre, até o
próximo level-up REAL, se algum dia acontecer no mesmo cliente já
observando).

Fix: em vez de pular a entrada colidida, MESCLA o level nela
(`_already` virou `dict[eid, entry]` — guarda a REFERÊNCIA do dict já
enfileirado, muta `entry["level"] = level` in-place em vez de decidir
"enfileira ou não").

**Validado**: `tests/test_server.py::TestRegressionBugs::
test_level_up_nao_some_quando_coincide_com_outro_broadcast_de_hp_no_mesmo_tick`
— pré-enfileira um broadcast de HP sem level (simulando dano PvP no
mesmo tick), força level-up, confirma que sai UMA mensagem só (sem
duplicar) e que ela TEM o level novo. Suíte completa 207/207, rodada 3x.

### 34.21 Loot não era free-for-all dentro do grupo (17/07/2026)

Usuário observou (não implementado na Fase E original): grupo deveria
ter loot free-for-all — qualquer membro pode lootear, não só quem
atacou primeiro.

Causa raiz: `server/loot_processor.py::request_loot()` só autorizava
`player_eid == corpse["owner_eid"]` (primeiro atacante, "dono" do
corpo) — sem nenhuma noção de grupo. E mesmo que autorizasse, só o dono
recebia `LOOT_AVAILABLE` (`server/session.py`) — o resto do grupo nunca
saberia que tinha itens pra pegar.

Fix:
- `request_loot()`: além do dono, autoriza qualquer player no MESMO
  `party_id` do dono (`get_party_id_of`). Fora do grupo do dono continua
  bloqueado, como sempre. Primeiro do grupo a lootear esvazia
  items/coins — quem tenta depois recebe `{items:[],coins:0}`, mesmo
  comportamento padrão de "free for all" de qualquer MMO (não
  implementado turno/prioridade de loot nesta leva).
- `server/session.py`: `LOOT_AVAILABLE` agora vai pra `get_party_members
  (owner_eid)` inteiro (ou só o dono, se ele não estiver em grupo) — cada
  membro recebe a notificação e a lista de itens independentemente.

**Validado**: `tests/test_party.py::TestPartyLootFreeForAll` (4 testes)
— membro do grupo consegue lootear o corpo do dono, fora do grupo
continua bloqueado, dois grupos diferentes não se misturam, segundo
membro do grupo recebe vazio depois do primeiro lootear. Suíte completa
195/195, rodada 3x.

**Não validado**: sessão manual com 2+ clientes agrupados — matar um
mob, os dois verem o corpo com itens disponíveis, qualquer um dos dois
conseguir lootear (não só quem bateu primeiro).

### 34.22 Nameplate trocado de MEGAMAN10 pra Determination (17/07/2026)

Usuário reportou incômodo visual: na fonte pixel MEGAMAN10 (usada nos
nomes acima da cabeça de player/mob/NPC desde 11/07/2026, `ui/
fonts.py::make_pixel`), o "g" minúsculo parece um "9" — quirk comum de
fontes pixel pequenas (descendente curto, curva fecha parecido).
Pedido do usuário: trocar pela Determination, a fonte principal do
projeto (`ui/fonts.py::make`, já usada em todo o resto da UI).

Fix: os 3 call sites de produção que usavam `make_pixel()` pra nome/
nível de nameplate passaram a usar `make()` (mesmo tamanho, 16 pro nome
e `LEVEL_FONT_SIZE` pro nível — `make()` já aplica a correção de escala
certa pra Determination, não precisou de ajuste extra):
- `client/remote_entity_handlers.py` — nome/nível de mob remoto e de
  player remoto (2 pares de font).
- `ui/systems.py::RenderSystem.__init__` — nome flutuante de NPC (e
  mob, no client online, reaproveitado de lá).

`make_pixel()`/MEGAMAN10.ttf **não foram removidos** — ficam como
utilitário "convive em paralelo" (mesmo espírito do comentário original
em `ui/fonts.py`), ainda exercidos por `tests/test_client_ui.py`
diretamente; só pararam de ser CHAMADOS pelos 3 sites de produção.
`render_tight()` (correção de bearing/kerning, `ui/world_labels.py::
add_text`) continua em uso pra QUALQUER fonte de nameplate — é genérica
(repacota pela tinta real + respiro fixo), não exclusiva da MEGAMAN10,
então não precisou ser removida/trocada.

**Validado**: suíte completa 196/196, rodada 3x (mudança é puramente
visual — sem teste automatizado dedicado, `tests/test_client_ui.py`
continua validando `make_pixel`/`render_tight` em isolamento, agora só
não mais wireados na nameplate).

**Não validado**: visual em jogo — "g" legível, espaçamento do
`render_tight` não ficando estranho pra Determination (fonte
proporcional, diferente da MEGAMAN10 quase-monoespaçada que motivou
aquele fix originalmente).

**Follow-up 1 (mesmo dia, `720eb2e`)**: usuário reportou "muito
pequena" depois do fix acima. Causa: `make()` aplica `_SCALE=0.5`
internamente (Determination renderiza ~2× mais alta que a fonte padrão
no mesmo size) — `make_pixel(16)` (sem correção nenhuma) virou
`make(16)` sem compensar, saindo com ~8pt reais em vez dos 16pt que a
MEGAMAN10 usava. Fix: dobrou o `size` (×2) nos 3 call sites pra manter
o tamanho visual equivalente ao anterior.

**Follow-up 2 (mesmo dia, `89841ad`)**: pedido separado do usuário — o
balão de fala (`ui/chat_bubble.py`) devia usar a MESMA fonte da janela
de chat, "parecia" a mesma só que bold. Eram o mesmo arquivo e mesmo
size NOMINAL (22), mas o balão criava sua própria instância via
`make(22)` fixo enquanto a janela de chat usa `font_sm`
(`make(UI.FONT_SM * _ui_scale)`) — escalas diferentes fazem uma fonte
sem antialiasing renderizar peso de traço visualmente diferente a cada
tamanho inteiro distinto. Fix: `ChatBubbleManager.set_font()` —
`GameEngine._reload_ui_fonts()` passa o próprio `self.font_sm` pro
balão usar o MESMO objeto de fonte, sempre em sincronia com qualquer
mudança de `_ui_scale`. Ver §34.23 pro follow-up seguinte (posição +
nitidez do balão).

### 34.23 Balão de fala: sobrepunha o nameplate e não era pixel-perfect (17/07/2026)

Terceiro follow-up da mesma sessão de ajustes de fonte (§34.22). Usuário
pediu: (1) mover o balão pra cima — texto caindo em cima do nameplate
(nome/nível/HP), ilegível; (2) fonte do balão não estava pixel-perfect
como a do chat.

Causa raiz ÚNICA pros dois: `ui/chat_bubble.py::ChatBubbleManager.render()`
blitava direto em `self._zoom_surf` (surface de MUNDO, pré-zoom) com um
gap FIXO (`GAP_ABOVE_HEAD`) do topo do sprite — sem nenhuma noção da
altura do nameplate (que é empilhado por `_draw_remote_players` via
`ui/world_labels.py::WORLD_LABELS`, variável conforme nome+nível+HP
bar). Dois sintomas da mesma causa: (a) gap fixo menor que a pilha real
→ balão sobrepõe; (b) `_zoom_surf` passa por `pygame.transform.scale()`
no fim do frame (zoom da câmera) — exatamente o bug documentado no topo
de `ui/world_labels.py` desde 11/07/2026 ("texto de fonte pixel-perfect
... sai BORRADO/DISTORCIDO quando redimensionado por zoom não-inteiro"),
mas o balão nunca tinha sido migrado pra lá.

Fix: `ChatBubbleManager.render()` não blita mais sozinho — constrói o
balão inteiro (fundo+borda+linhas) como UMA Surface e enfileira via
`WORLD_LABELS.add_icon(pos.x, world_y_top, bubble_surf,
stack_key=entity_id, gap_before=...)`, MESMO `stack_key` que o
nameplate já usa (`entity_id`/`local_eid`). Como `_draw_remote_players`/
`_draw_mob_hp_bars` (que enfileiram o nameplate) já rodam ANTES no
`game.py` (mesmo ponto de chamada de sempre, só mudou o que a função
faz por dentro), `WORLD_LABELS._stack_offset` já reflete a altura do
nameplate quando o balão é enfileirado — empilha automaticamente ACIMA,
qualquer que seja a altura real (sem gap fixo pra manter sincronizado).
E como `WORLD_LABELS.render()` desenha em `self.screen` (screen-space,
NUNCA passa pelo scale do zoom — mesma garantia que já vale pro
nameplate), o texto sai tão nítido quanto o da janela de chat, de
graça. `set_alpha()` do fade-out (últimos 1s de vida do balão) segue
funcionando — muta a Surface cacheada ANTES de enfileirar a cada
frame, não é a Surface compartilhada de `CachedFont` (regra de "não
mutar" em `ui/fonts.py` não se aplica aqui).

**Validado**: suíte completa 196/196, rodada 3x (mudança visual, sem
teste dedicado — `WORLD_LABELS` é mecanismo já usado e testado
indiretamente pelo resto do nameplate).

**Não validado**: visual em jogo — balão não sobrepõe mais nome/HP
mesmo com pilha alta (ex: player com efeitos extras), texto nítido em
qualquer zoom não-inteiro, fade-out continua suave.

**Follow-up (mesmo dia)**: usuário comparou nameplate vs chat lado a
lado (print) e confirmou que o nameplate ainda parecia "bold" mesmo já
na Determination. Causa raiz: `ui/world_labels.py::add_text()`
(caminho de TODO nome de player/mob/NPC acima da cabeça) sempre
renderizava via `render_tight()` — função criada 11/07/2026
especificamente pra corrigir o bearing esquerdo desproporcional da
MEGAMAN10 ("Zumbi" virava "Zumb i"), que empacota glifo por glifo com
só **1px de respiro FIXO** entre a tinta real, descartando o
kerning/bearing natural da fonte. Pra uma fonte PROPORCIONAL como a
Determination (usada pelo chat via `font.render()` normal, espaçamento
correto), esse 1px fixo é bem menor que o espaçamento natural — letras
ficavam empacotadas demais, lido como "bold" por comparação direta com
o chat.

Fix: `add_text()` agora chama `font.render(text, False, color)`
direto, igual o chat — sem bug de fonte pra corrigir na Determination,
não tem motivo pra reempacotar. `render_tight()` não foi removida (seu
motivo original de existir, MEGAMAN10, continua no arquivo como
utilitário "convive em paralelo" — mesmo padrão do §34.22).

**Validado**: suíte completa 196/196, rodada 3x.

**Não validado**: visual em jogo — nameplate com o mesmo peso de traço
do chat, espaçamento entre letras natural (nem apertado nem com vão
estranho tipo o bug original da MEGAMAN10).

**Follow-up final (mesmo dia)**: mesmo depois do fix de `render_tight`,
usuário comparou lado a lado de novo (print com "Juugomage" no
nameplate vs "Juugomage:" no chat) e pediu de forma explícita: "quero
que a fonte fique no mesmo estilo do nick que está no chat". Causa
residual: nameplate usava `make(32)` (uma instância PRÓPRIA, criada só
pra igualar o tamanho visual da antiga MEGAMAN10) — mesma fonte/render,
mas tamanho (~16pt efetivo) MAIOR que o do chat (`font_sm`, ~11pt
efetivo) — grande o bastante pra ainda ler como "outro estilo" numa
comparação direta.

Fix definitivo: nameplate de player/mob/NPC para de criar fonte
PRÓPRIA — usa o MESMO objeto `self.font_sm` da janela de chat.
- `client/remote_entity_handlers.py` (mob remoto, player remoto): passa
  a ler `self.font_sm` direto no `add_text()` a cada frame (sem cache
  numa instância própria) — GameEngine já recria `font_sm` em
  `_reload_ui_fonts()`, então fica sempre em sincronia com `_ui_scale`
  de graça, sem precisar de nenhum mecanismo de invalidação.
- `ui/systems.py::RenderSystem` (NPC/mob local, offline) é uma classe
  SEPARADA (não mixin de `GameEngine`, sem acesso a `self.font_sm`) —
  ganhou `set_name_font(font)`; `game.py` injeta `self.font_sm` logo
  após construir o `RenderSystem` E de novo a cada
  `_reload_ui_fonts()` (mesmo padrão do `CHAT_BUBBLE.set_font()`,
  §34.23 acima — sem isso, mudar `_ui_scale` deixaria a fonte da
  nameplate dessincronizada da do chat de novo).

Fonte do NÍVEL (número dentro do círculo) NÃO mudou — continua com
tamanho próprio (`LEVEL_FONT_SIZE * 2`), o pedido do usuário era
especificamente sobre o NOME.

**Validado**: suíte completa 196/196, rodada 3x.

**Não validado**: visual em jogo — nameplate literalmente no mesmo
tamanho/peso do chat lado a lado; mudar a escala de UI (menu de opções)
mantém os dois em sincronia.

### 34.24 Loot free-for-all duplicava ouro/itens entre membros do grupo (17/07/2026)

Usuário validou §34.21 (loot free-for-all) e achou o bug real: mago e
arqueiro no mesmo grupo matam um zumbi, mago lootea 11 de ouro, arqueiro
abre o MESMO corpo e o ouro ainda aparece lá (deveria ter sumido).

Causa raiz — bem mais funda que §34.21: o loot em modo online **sempre
foi 100% client-autoritativo**, apesar do protocolo `LOOT_REQUEST`/
`LOOT_RESULT` já existir completo no servidor (`server/session.py::
_handle_loot_request` → `WorldServer.request_loot()`, já corretamente
esvazia o corpo no primeiro saque — validado por
`TestPartyLootFreeForAll`). O cliente NUNCA mandava `LOOT_REQUEST`:
`client/save_sync_handlers.py::_send_loot_request` existia mas era
**código morto**, zero call sites. O fluxo real: `LOOT_AVAILABLE` cria
uma entidade `Corpse` ECS LOCAL com os itens/coins (mecanismo
compartilhado com o singleplayer, comentário original: "LootSystem
offline funcionar IDENTICAMENTE ao offline"); `ui/systems.py::
LootSystem._try_take_item` credita `Wallet`/`Inventory` **direto da
cópia local**, sem round-trip nenhum; só DEPOIS manda `GOLD_UPDATE`/
`INV_SYNC` informando o servidor do novo total (client dita, servidor
só registra — sem validar a quantidade). Antes da Fase E isso era
inofensivo por acidente: só o first-attacker recebia `LOOT_AVAILABLE`
(um destinatário só, sem como duplicar). §34.21 mandou `LOOT_AVAILABLE`
pro GRUPO INTEIRO — cada membro passou a ter sua PRÓPRIA cópia local
completa e processá-la de forma 100% independente, sem nenhuma
sincronização entre clientes.

Fix — ativa o protocolo que já existia (nenhuma mensagem nova):
- `ui/systems.py::LootSystem` ganhou `_online_loot_requester` (setado
  só em modo online) + `_online_loot_pending` (evita reenvio enquanto
  uma resposta está em voo). Clicar em ouro OU item, com o requester
  setado, NÃO credita nada localmente — manda o request e retorna.
  Mesma proteção em `_try_equip_item` (equipar direto do loot): cai pro
  fluxo de request também (trade-off: item vai pro inventário, não
  equipa direto — aceitável por segurança).
- `client/save_sync_handlers.py::_send_loot_request_for_local_corpse`
  (novo) — ponte entre o eid LOCAL que o `LootSystem` conhece e o
  `corpse_id` do SERVIDOR que o protocolo espera (espaços de id
  diferentes; resolve via `self._available_loot`, o dict que já mapeia
  um pro outro desde `LOOT_AVAILABLE`).
- `client/network_handlers.py::_handle_msg_loot_result` — antes só
  limpava a entidade local; agora é onde o crédito REAL acontece,
  usando o que o servidor confirma que sobrava (reconstrói itens via
  `content.loot_tables._T`/`QUEST_ITEMS`, mesmo padrão de
  `_handle_msg_loot_available`). Se `coins==0` e `items==[]` (outro
  membro já pegou tudo), mostra "Já foi saqueado" em vez de creditar
  nada.
- `game.py`: injeta o requester no `LootSystem` junto da wiring online
  já existente (`_on_loot_collected`).

**Validado**: `tests/test_client_ui.py` ganhou 3 testes — modo online
não credita localmente e manda o request certo (eid local do corpse);
clique duplicado enquanto o request está em voo não reenvia; modo
offline/legado (sem requester setado) continua creditando local igual
sempre foi (regressão). Suíte completa 199/199, rodada 3x.

**Não validado**: sessão manual com 2+ clientes agrupados — mago
lootea o ouro, arqueiro abre o MESMO corpo e vê vazio ("Já foi
saqueado"); item também não duplica; modo offline/singleplayer (se
algum dia rodar de novo) continua funcionando sem regressão.

**Follow-up (mesmo dia)**: usuário testou o fix acima e achou outro
sintoma da mesma raiz: "o corpo com loot some quando eu looteio o gold,
se tiver mais algum drop os players perdem a chance de lootear". Causa:
`request_loot()` (mesmo já corrigido pro grupo) continuava "tudo ou
nada" — sacar o ouro esvaziava items JUNTO na mesma chamada. E
`server/session.py::_handle_loot_request` mandava `ENTITY_DESPAWN` do
corpo pra TODO MUNDO no AOI incondicionalmente em QUALQUER saque
bem-sucedido, mesmo sobrando loot — o corpo "sumia" da tela de todo o
grupo mesmo com itens ainda dentro.

Fix — protocolo `LOOT_REQUEST` ganhou `take` (`"gold"`/`"item"`/`"all"`)
+ `item_name` (não índice — quebraria se outro membro do grupo já
tivesse tirado algo antes, deslocando a lista):
- `server/loot_processor.py::request_loot()` — `take="gold"` só mexe em
  `coins`; `take="item"` remove só o PRIMEIRO item da lista atual com
  aquele nome; `"all"` continua existindo (compat, não usado pelo
  cliente).
- `server/session.py::_handle_loot_request` — `ENTITY_DESPAWN` só sai
  quando o corpo fica REALMENTE vazio (`not items and coins<=0`), não
  mais em toda resposta.
- `ui/systems.py::LootSystem._try_send_online_loot_request(take,
  item_name)` — clicar ouro manda `take="gold"`; clicar item (inclusive
  `_try_equip_item`, botão direito) manda `take="item"` + `item.name`.
- `client/network_handlers.py::_handle_msg_loot_result` — em vez de
  remover a entidade `Corpse` LOCAL incondicionalmente, agora remove só
  o que veio confirmado (`corpse_comp.coins=0` / pop do item por nome) e
  só fecha/remove a entidade quando ela fica REALMENTE vazia — o resto
  do loot continua visível e lootável (pelo mesmo player ou por
  qualquer um do grupo).

**Validado**: `tests/test_party.py::TestLootGranular` (4 testes) —
sacar ouro não leva item junto (e vice-versa), sacar item por nome
remove só aquele (com outro item presente permanecendo intocado), pedir
de novo o mesmo item já retirado volta vazio. `tests/test_client_ui.py`
ganhou o teste de clicar item mandando `take="item"`+nome certo. Suíte
completa 204/204, rodada 3x.

**Não validado**: sessão manual — sacar só o ouro deixa o corpo aberto
com os itens ainda lá (pro mesmo player E pro resto do grupo); sacar
tudo aos poucos até esvaziar de verdade some o corpo pra todo mundo.

**Follow-up (18/07/2026)**: usuário testou de novo e achou o pedaço que
faltava — o `take` granular resolveu "sacar ouro não deveria levar
item", mas SÓ pra quem clicou. Repro relatado: A saca o ouro; B (que
também tem o MESMO corpse aberto, via `LOOT_AVAILABLE`) continua vendo
o ouro lá — "fantasma", já pego. B clica no ouro fantasma → volta vazio
→ "buga e fecha o loot, e o loot some" (mesmo com o item ainda por
pegar). Mesma coisa acontecia se B pegasse o item primeiro.

Causa raiz: `LOOT_RESULT` (resposta ao `LOOT_REQUEST`) só ia pro
REQUISITANTE — ninguém mais do grupo era avisado que o corpse mudou.
Cada cliente só corrige a própria cópia LOCAL quando recebe uma
resposta ao PRÓPRIO clique; sem clicar em nada, B nunca saberia que A
já tinha levado o ouro. Pior: no handler de resultado, `corpse_comp.
coins` só era zerado `if coins > 0` (== "eu recebi ouro agora") — uma
resposta vazia (`coins=0`, porque outro já pegou) NUNCA corrigia o
valor antigo, deixando o ouro "fantasma" preso pra sempre até o timer
de decay do corpse (até 120s).

Fix: novo `LOOT_UPDATE` (S→C) — `server/session.py::
_handle_loot_request`, depois de responder `LOOT_RESULT` pro
requisitante, avisa TODO o resto do grupo do dono do corpse (exceto
quem acabou de sacar) com `{corpse_id, coins_taken, item_names_taken}`.
Cliente (`client/network_handlers.py::_handle_msg_loot_update`) só
SINCRONIZA a cópia local (zera coins/remove item por nome) — nunca
credita Wallet/Inventory, já que quem recebe isso não pegou nada, só
está sendo avisado que sumiu. Lógica de "zerar/remover e decidir se
ainda sobra loot" foi extraída pra um helper compartilhado
(`_sync_local_corpse_after_take`) usado tanto pelo `LOOT_RESULT`
(minha própria resposta) quanto pelo `LOOT_UPDATE` (resposta de
outro) — elimina a classe inteira de "fantasma nunca corrigido".

**Validado**: `tests/test_session.py::TestPartyLootSync` (3 testes,
`IsolatedAsyncioTestCase` com `fake_login`/duas sessões reais) — B
recebe `LOOT_UPDATE` com `coins_taken` correto quando A saca; quem
sacou NÃO recebe `LOOT_UPDATE` de volta (só `LOOT_RESULT`); sem grupo,
ninguém mais recebe nada. Suíte completa 207/207, rodada 3x.

**Não validado**: sessão manual com 2+ clientes agrupados — A saca o
ouro, B (com o loot já aberto, sem clicar em nada) vê o ouro sumir da
janela em tempo real; item continua disponível pros dois até alguém
pegar; corpo só some de verdade quando fica realmente vazio pros dois
lados.

**Validado (18/07/2026, sessão seguinte)**: usuário testou tudo acima
em jogo — "Sobre o loot, validei e está tudo certo". Fecha a thread de
loot; nenhuma ação pendente.

### §34.25 — Level-sync: causa raiz real (reconexão sem reiniciar o cliente) + gap do frame de grupo

Depois de 4 rounds de fix em `_sync_player_hp_dirty` (§34.20/§34.24),
usuário reproduziu com precisão: logou com 2 contas, e o level de um
player remoto só "acertava" quando o HP dele mudava (regen tick ou
dano de mob) — nunca de forma independente. Concluiu (corretamente)
que era um problema arquitetural, não mais um bug pontual, e pediu
pesquisa de como MMOs tratam esse tipo de sincronização antes de
qualquer novo patch.

**Pesquisa (web)**: o padrão-mestre da indústria (Unreal `PlayerState`/
`OnRep_MyProperty`, replicação por "dirty flag" genérico) é: cada campo
replicado tem seu PRÓPRIO gatilho de mudança, independente de qualquer
OUTRO campo — nunca "campo B só é reenviado quando o campo A muda".
Confirma que empacotar `level` dentro da tupla de dirty-check do HP
(mesmo já capturando corretamente uma mudança de level sozinha, ver
auditoria abaixo) era a escolha arquitetural errada — mistura dois
conceitos que deveriam ser independentes.

**Auditoria de código** (antes de mexer): reli as 3 correções
anteriores (`_sync_player_hp_dirty`, WORLD_STATE-no-login em
`_spawn_and_start`, "player ficou visível" em `_handle_msg_aoi_update`)
— todas continuam corretas e usam o componente VIVO, não snapshot de
login. A tupla `(hp, hp_max, level)` já detecta uma mudança de level
MESMO sem hp/max_hp mudarem (a tupla inteira difere do cache). Ou
seja: o mecanismo de broadcast, no papel, já não dependia de HP mudar.

**Causa raiz real encontrada**: `client/network_handlers.py::
_spawn_remote_player_entity` tem um guard `if server_eid in
self._remote_players: return` — e NADA no cliente limpava
`self._remote_players` (nem destruía as entidades ECS locais) ao
reconectar/relogar no MESMO processo do jogo. Se o servidor reiniciar
entre sessões de teste (fluxo comum enquanto o usuário testa) e
reciclar os mesmos `server_eid` sequenciais, o cliente acha que a
entidade "já existe" e nunca aplica o payload novo (level/hp/nome
atuais) — a entidade antiga (com o level da sessão de teste ANTERIOR)
sobrevive até algo que sobrescreva por inteiro via `STATS_UPDATE`
(ex: HP mudando), o que parecia exatamente "level só atualiza quando o
HP muda".

Fix: `_handle_msg_login_ok` agora destrói as entidades ECS de todo
`self._remote_players` e limpa esse dict + `_remote_player_move_queues`
+ `_remote_step_timers` logo no início — toda sessão de LOGIN_OK (seja
o primeiro login do processo ou um relogin) começa com espelhamento de
players remotos zerado, igual uma reconexão de verdade deveria.

**Gap separado, também relatado pelo usuário**: level nos slots do
frame de grupo não atualizava mesmo quando o nameplate atualizava.
Causa: `PARTY_STATE` (que alimenta `client/party_handlers.py::
_handle_msg_party_state`) só é reenviado em eventos de COMPOSIÇÃO do
grupo (entrar/sair/expulsar/promoção) — nunca em mudança de level de um
membro. Fix: `_sync_player_hp_dirty`, ao detectar mudança de level,
agora também enfileira o `party_id` do player (se houver) em
`_party_state_events_this_tick` — reusa o mesmo pipe que já existe
pra reenviar `PARTY_STATE` a todo o grupo, sem pipeline novo.

**Validado**: `tests/test_server.py::
test_level_up_de_membro_do_grupo_marca_party_state_sujo` — level-up de
um membro marca o grupo como sujo em `consume_party_state_events()`.
Suíte completa 209/209, rodada 3x. O fix de `_handle_msg_login_ok`
(destruir entidades remotas antigas no login) não tem teste automatizado
— exigiria simular uma reconexão completa de `GameEngine`, sem
precedente nos testes de cliente existentes; fica pendente de validação
manual (relogar 2x no mesmo processo sem reiniciar o cliente, servidor
reiniciado no meio).

**Não validado**: sessão manual — relogar (sem fechar o client) depois
de reiniciar o servidor não deveria mais mostrar level/hp/nome
desatualizado de nenhum player remoto; level de membro do grupo deve
atualizar no frame no mesmo momento em que atualiza no nameplate.

**Follow-up (mesmo dia) — causa raiz real, mais simples**: usuário
testou o fix acima. Frame de grupo: correto pros 2 imediatamente. Mas
nameplate: level só atualizou depois de batalhar com um mob (dano →
STATS_UPDATE) — reproduzindo o sintoma de sempre, e perguntou
diretamente: "Não é possível atualizar o level no momento que o player
entra na AOI do outro player? Assim como atualiza a posição?"

Essa pergunta apontou pro lugar certo. Reauditoria dos handlers
`ENTITY_SPAWN`/`WORLD_STATE`/`AOI_UPDATE` **no cliente** (não no
servidor — esses já estavam corretos há 4 rounds) achou o bug real:
`client/network_handlers.py::_handle_msg_world_state`,
`_handle_msg_entity_spawn` e `_handle_msg_aoi_update` (bloco
"spawned") reconstroem manualmente um dict pra passar pro
`RemoteEntityHandlers._spawn_remote_player_entity(server_eid, data)` —
e os 3 esqueciam de repassar o campo `"level"` que o payload do
servidor **já continha corretamente** desde os fixes anteriores. Como
`_spawn_remote_player_entity` lê `data.get("level", 1)`, toda entidade
remota nascia sempre com level 1 (ou, em versões anteriores desta
sessão, com o level de uma entidade reciclada) — e só era corrigida
depois por um `STATS_UPDATE` de HP (o único handler que de fato
aplicava `payload["level"]` a `rc.level`). Isso explica TODOS os
sintomas reportados nesta thread inteira, de forma muito mais simples
que a teoria de reciclagem de eid do follow-up anterior (que também
era um bug real e válido de se corrigir, só não era a causa
predominante deste sintoma específico).

Fix: os 3 call sites agora incluem `"level": <campo>.get("level", 1)`
no dict passado pra `_spawn_remote_player_entity` — nível chega junto
com posição/hp/nome no exato momento em que a entidade nasce (seja no
login, seja ao entrar na AOI por movimento), sem depender de nenhum
evento de HP subsequente.

**Validado**: `tests/test_client_ui.py` ganhou 3 testes (fixture
`_NetHandlerFixture`, combina `NetworkHandlers`+`RemoteEntityHandlers`
sem precisar de `GameEngine` completo) — `ENTITY_SPAWN`, `WORLD_STATE`
e `AOI_UPDATE` (bloco spawned) cada um propaga o level do payload pro
`RemoteControlled.level` da entidade recém-criada. Suíte completa
212/212, rodada 3x.

**Validado (18/07/2026)**: usuário testou em jogo e confirmou — "Validado."
Fecha a thread de level-sync inteira (4 rounds de fix + a causa raiz real).

---

### §34.26 — Zona PvP (Fase F do roadmap, 18/07/2026)

Próximo item do roadmap combinado com o usuário depois da Leva 1 e da
Fase E (Party): **Fase F — Zonas PvP**, comportamento já decidido em
§34.13/§34.19 antes mesmo da Fase E começar: "solo = todos hostis; em
party = só quem está fora do grupo". Party era pré-requisito explícito —
sem `get_party_id_of()` não dava pra implementar a exceção de grupo.

Decisões do usuário: (1) 1ª zona fica numa área nova dentro do `map_1`
(mundo aberto), não uma caverna inteira nem só o mecanismo sem conteúdo;
(2) indicador visual = mensagem no log de entrada/saída **+** banner
persistente na tela enquanto dentro (não só o log).

Achado-chave da exploração: zona PvP **não precisa de estado próprio nem
de checagem por tick** — diferente de duelo (que precisa de `_duel_pairs`
+ `_tick_duel_distance_check` porque é um acordo persistente que pode
ficar "pendurado" até os players se afastarem), zona é um predicado 100%
computado a partir da posição atual. `can_engage()` já é consultado a
CADA tentativa de ataque (`server/combat_processor.py`,
`server/skill_processor.py`, `server/spell_completion_processor.py`),
então sair da zona no meio de uma luta já bloqueia o próximo golpe
automaticamente — nenhum código novo de "encerrar combate" foi
necessário.

Auditoria confirmou que morte de player hoje não dropa gold/item nem
penaliza XP em NENHUM caso (nem PvE nem duelo) — uma morte PvP em zona
reusa o pipeline de morte existente (`server/respawn_system.py::
_handle_player_death`, fantasma/respawn, sem lethal interceptor como o
duelo) sem NENHUMA mudança, e já sai consequence-free de graça.

**Servidor**:
- Geometria da zona é **retângulo de tiles por mapa**, declarada em
  `pvp_zones` no `_entities.json` do mapa (mesmo padrão de
  `ambient_zones`, já usado pra som ambiente) — `engine/map_loader.py::
  _merge_entities_json` ganhou o parse; `maps/map_1_entities.json` ganhou
  a 1ª zona ("Arena Selvagem", rect `[180, 389, 196, 400]`, área aberta
  ao sul de uma casa perto do spawn (115,389) — coordenadas de 1ª leva,
  ajuste é só JSON, sem código).
- `server/pvp_zone_processor.py` (novo, `PvpZoneProcessorMixin`, SEM
  estado de pares/tick-check) — `_in_pvp_zone(eid)` (rect containment via
  `get_entity_map` + `TileMovement.current_tile_x/y`) e
  `_pvp_zone_allows(attacker_id, target_id)` (ambos dentro da zona E não
  no mesmo grupo, via `get_party_id_of` já existente do
  `PartyProcessorMixin`).
- `server/world_server.py`: `_pvp_zones_by_map: dict[str, list[dict]]`
  populado em `_load_map_for` (logo após `load_map_csv`);
  `_pvp_allowed_between` ganhou o `or`-clause que já estava reservado por
  comentário desde a Leva 1 ("futuro: zona PvP da posição dos dois +
  exceção de party").

**Cliente**: `client/pvp_zone_handlers.py` (novo, `PvpZoneHandlers`) —
`_load_pvp_zones`/`_update_pvp_zone_indicator` espelham
`_load_ambient_zones`/`_update_ambient_zone` byte a byte (mesmo
algoritmo de rect containment com detecção de MUDANÇA de estado);
`_draw_pvp_zone_banner` desenha "ZONA PVP" fixo no topo-centro enquanto
dentro. **Nenhuma mensagem de rede nova** — a geometria da zona vem do
MESMO `_entities.json` que o cliente já carrega localmente pra
`ambient_zones`, então não precisa trafegar; a decisão de dano continua
100% autoritativa no servidor via `can_engage`, o indicador é puramente
cosmético.

**Validado**: `tests/test_pvp_zone.py` (7 testes) — dois sem grupo dentro
da zona se engajam nos dois sentidos; um dentro/um fora continua
amigável; mesmo grupo dentro da zona continua amigável (exceção);
grupos DIFERENTES dentro da zona são hostis entre si; fora de qualquer
zona sem duelo/grupo continua amigável (regressão da Leva 1); mapa sem
`pvp_zones` (cavernas) não quebra; duelo continua funcionando
independente da zona (regressão). `tests/test_client_ui.py` ganhou 2
testes do indicador (entra/sai alterna a flag do banner sem "piscar"
dentro do mesmo estado; mapa sem zonas nunca liga a flag). Suíte
completa 221/221, rodada 3x.

**Não validado**: sessão manual com 2 clientes — os dois entram juntos
no retângulo sem grupo e conseguem se atacar; um sai da zona no meio da
luta e o próximo golpe já é bloqueado sem reconectar; agrupados (mesmo
dentro da zona) NÃO conseguem se atacar; fora da zona segue amigável
(duelo por convite continua funcionando); morte na zona segue o ciclo
normal de fantasma/respawn sem perda de gold/item/XP; banner "ZONA PVP"
aparece ao entrar e some ao sair, log mostra as duas mensagens.

**Follow-up (mesmo dia)**: usuário testou dentro da zona (banner "ZONA
PVP" confirmado no topo) e reportou que os 2 players continuavam
amigáveis — clique direito abria o modal de trade/duelo/seguir em vez de
atacar, e castar skill contra o alvo retornava "Alvo amigável".

Causa raiz: `can_engage()` é usado tanto no SERVIDOR (decide dano de
verdade) quanto no CLIENTE (decide UX local — clique direito ataca vs
abre modal, valida skill ANTES de mandar `CAST_SKILL` pro servidor,
SPACE engaja). Cada lado roda no seu próprio processo Python, então
`engine.faction_system._pvp_context_resolver` é um global DIFERENTE em
cada um — o resolver do servidor (`_pvp_allowed_between`, já corrigido
acima) nunca foi o problema; o resolver do CLIENTE só sabia sobre
DUELO (`client/duel_handlers.py` registrava um resolver temporário só
com o oponente do duelo, via `register_pvp_context`, enquanto durava) —
nunca soube nada sobre zona PvP. O servidor liberava corretamente, mas
o cliente barrava a AÇÃO antes de sequer mandar a mensagem.

Fix: como `register_pvp_context` só guarda UM slot (não é uma lista
componível como o `_pvp_allowed_between` do servidor), a composição
"duelo OU zona" agora mora numa função ÚNICA, `game.py::
GameEngine._client_pvp_context`, registrada UMA VEZ em
`_load_map_and_entities` (não mais registrada/desregistrada a cada
início/fim de duelo — `_handle_msg_duel_start`/`_handle_msg_duel_end`
só atualizam o estado que o composto lê, `_duel_opponent_local_eid`).
A parte de zona reusa a MESMA geometria (`self._pvp_zones`) já usada
pelo banner, resolve o server_eid de cada lado do par via
`_local_eid_to_server_eid` (próprio player → `self._my_eid`; remoto →
`RemoteControlled.server_eid`) e aplica a mesma exceção de grupo
(`self._party_members`) que o servidor já aplicava. Continua sendo
PURA decisão de UX — o servidor permanece a única autoridade real
sobre dano.

**Validado**: `tests/test_client_ui.py` ganhou 4 testes
(`_PvpCtxFixture`, combina `DuelHandlers`+`PvpZoneHandlers`+
`PartyHandlers` com os 2 métodos de `GameEngine` vinculados, sem
precisar de `GameEngine` completo) — ambos dentro da zona libera; um
fora bloqueia; mesmo grupo dentro da zona continua bloqueando (exceção);
duelo libera independente de estar dentro ou fora da zona. Suíte
completa 225/225, rodada 3x.

**Validado (18/07/2026)**: usuário testou em jogo — 2 players únicos
dentro da zona se atacam; os 2 no mesmo grupo NÃO se atacam; e (com
outros testers, via build `release_tools/build_client.ps1`) 2 GRUPOS
diferentes dentro da zona se atacam entre si normalmente. Fecha a
thread de Zona PvP (Fase F) inteira — arquitetura + causa raiz do
resolver client-side + os 3 cenários de grupo.

---

### §34.27 — Fase G leva 1: Arena 2x2 (instanciamento + time por Facção + fila FIFO, 19/07/2026)

Antes de começar a Fase G ("Times/arenas") como planejada originalmente,
o usuário reconsiderou a ordem: tudo até aqui (contexto PvP, duelo,
Party, Zona PvP) vinha montando a base pra PvP ranqueado/instanciado
(arenas 2x2/3x3, campos de batalha 5x5 MOBA e outros — captura de
bandeira/base), e "Times" como proposto originalmente (criar time,
entrar na fila, fila forma os times) é na verdade a ETAPA FINAL de
integração, não o próximo passo. Ordem revisada e aprovada: (1)
instanciamento, (2) time via Facção, (3) ciclo de vida de partida
mínimo validado com o modo mais simples (Arena 2x2, só eliminação, sem
objetivo), (4) fila básica. Torres/campos de batalha/matchmaking solo
real ficam para próximas levas.

**Achado-chave (evita reinventar/duplicar)**: a abordagem ingênua — um
`WorldServer` inteiro por partida — quebraria, porque há 4 globais em
nível de MÓDULO que um `WorldServer` registra uma vez no boot e nunca
desregistra (`engine/world_systems.py::_svc`, `engine/faction_system.py::
_pvp_context_resolver`, `engine/core_systems.py::_lethal_interceptor`,
`engine/quest_events.py::_quest_system_ref`) — um segundo `WorldServer`
no mesmo processo sobrescreveria os 3 primeiros silenciosamente
(last-write-wins), vazando pathfinding/PvP/interceptor de morte entre
partidas concorrentes. Fix: continuar com UM `WorldServer` só (como
sempre foi) e generalizar o mecanismo que JÁ isola múltiplos mapas
dentro dele — `_map_bundles`, `MapLocation`, `_pvp_zones_by_map` já são
chaveados por uma STRING (nunca precisou ser literalmente um caminho de
arquivo). `WorldServer._load_map_for` ganhou um parâmetro opcional
`instance_key`: quando fornecido, toda a chave de isolamento usa essa
string sintética (`f"{template}::{match_id}"`) em vez do `map_file`
real — `template_file` continua sendo o único lido do disco e o único
mandado pro cliente (`_template_file_of`, faz o "de-para"). `_load_instance`/
`_unload_instance` (novos) são wrappers finos disso. `find_path()`/
`get_tilemap()` (sem eid na assinatura) continuam cobertos só pela regra
existente de `register_map_services_for` (CLAUDE.md) — não precisaram de
mudança, já são chamados corretamente em todo entry point de player
por convenção mandatória de longa data.

**Time por Facção, não por contexto PvP**: diferente de duelo/zona
(exceção "amigável + contexto libera"), arena atribui um componente
`Faction("arena_time_a"/"arena_time_b")` ao player (sobrescreve
`PLAYER_FACTION` enquanto a partida dura — `engine/components.py::
Faction`, docstring atualizada) — a relação já sai "hostil" de verdade
(`content/faction_data.py`, novo par explícito, embora "neutro"
já bastasse pro `can_engage` liberar; hostil é só pra nameplate ficar
vermelha) e `can_engage()` libera SEM NUNCA consultar o resolver de
contexto PvP — zero mudança em `_pvp_allowed_between` pra isso (só um
comentário explicando por quê). Confirma o que o comentário antigo
"times/MOBA nem passam por aqui" já dizia desde a Fase 4.

**"Time" não tem estado próprio nesta leva** — decisão de engenharia
pra não criar abstração cedo demais: o time É o Party (grupo) que
entrou na fila junto (exatamente 2 membros, decisão do usuário). Se uma
leva futura precisar de time SEM grupo pré-formado (matchmaking solo
real formando o time), aí sim vale a pena um `TeamProcessorMixin`
próprio — até lá seria abstração sem uso real.

**Eliminação, não morte de verdade**: reusa o MESMO hook de golpe letal
que o duelo já usa (`engine.core_systems.register_lethal_interceptor`)
— o slot é ÚNICO (não uma lista componível), então a composição
duelo-ou-arena mora em `WorldServer._lethal_interceptor_composite`
(novo), registrado uma vez só em `_load_all_maps` no lugar de
`self._duel_lethal_interceptor` direto. Golpe que mataria: alvo fica em
1 HP (mesmo valor hardcoded que `apply_damage_core` já usa pro duelo —
não dá pra escolher outro) + `CombatState.is_immune=True` (não pode
levar mais dano) em vez de morrer/virar fantasma. Time com todos os
membros eliminados perde — desconexão em partida ativa conta como
eliminação também (`end_matches_of`, chamado no disconnect ANTES do
despawn, mesmo ponto de `end_duels_of`/`end_parties_of`).

**Teleporte de entrada/saída reusa 100% o que já existia**:
`WorldServer.transfer_player` (o mesmo usado por `ZONE_CHANGE_REQ` de
transição de caverna) já faz tudo — atualiza `_player_maps`,
`MapLocation`, `snap_to_tile`. O cliente reusa `ZONE_CHANGE` +
`_do_transition` (o MESMO fluxo de transição de caverna) — carrega o
mapa novo do zero, limpa entidades remotas, reposiciona. **Zero código
novo no cliente pra troca de mapa** — `ARENA_MATCH_START`/`ARENA_MATCH_END`
(novos, mandados junto do `ZONE_CHANGE`) são só informativos (contexto
da partida/resultado), não fazem a troca de mapa sozinhos.

**Conteúdo novo**: `maps/arena_2v2.csv` (20×20 tiles, borda de parede,
interior piso de pedra — placeholder funcional, sem entities.json).
Spawns de cada time são constantes em `server/match_processor.py`
(YAGNI — só existe 1 template, não compensa um schema de "team spawn"
genérico ainda).

**Servidor**: `server/match_processor.py` (novo, `MatchProcessorMixin`)
— fila FIFO de `party_id` (`_arena_queue_2v2`), pareamento por tick
(`_tick_arena_queue`, mesmo padrão de `_tick_duel_distance_check`),
`_create_match`/`_eliminate_player`/`_end_match`, interceptor de golpe
letal, eventos por tick (`consume_arena_match_start/end_events`)
consumidos pelo broadcast loop do `SessionManager`.

**Protocolo**: `ARENA_QUEUE_JOIN`/`ARENA_QUEUE_LEAVE` (C→S, só líder de
grupo de 2), `ARENA_QUEUE_STATE` (S→C), `ARENA_MATCH_START`/
`ARENA_MATCH_END` (S→C, mandados junto do `ZONE_CHANGE`).

**Cliente**: `client/arena_handlers.py` (novo, `ArenaHandlers`) — botão
"Fila de Arena 2x2" logo abaixo do frame de grupo (só líder de grupo de
2 vê), avisos de entrada na fila/início/fim de partida.

**Validado**: `tests/test_arena.py` (17 testes) — fila FIFO pareia
corretamente, só líder/só grupo de 2 entram; time ganha Facção oposta;
`can_engage` libera entre times opostos e bloqueia mesmo time; golpe
letal não mata de verdade (marca eliminado, imune); eliminar o time
inteiro termina a partida e restaura Facção/mapa/posição de todos;
desconexão em partida conta como eliminação; **2 partidas SIMULTÂNEAS
da MESMA arena não vazam tile/pathfinding entre si** (prova direta do
achado-chave); duelo e mundo aberto continuam funcionando com o
interceptor composto. Suíte completa 260/260, rodada 3x. Testado também
via script manual (fora da suíte) rodando o fluxo completo através de
`apply_damage_core` de verdade (não só chamando os métodos internos
diretamente).

**Não validado**: sessão manual com 4 clientes reais (2 grupos de 2) —
entrar na fila, partida formada automaticamente, teleporte visual pros
dois lados, combate só contra o time adversário, fim de partida
restaura tudo, banner/log de vitória-derrota aparece.

---

### §34.28 — Validação de nome de personagem (formato + unicidade global) + gerador de nome sugerido (20/07/2026)

Personagens de teste do próprio usuário acumularam vários "Aventureiro"/
"Juugo" duplicados — não havia NENHUMA validação de nome no banco:
`server/auth.py::_create_character_sync` fazia `INSERT` direto, e a
caixa de texto do cliente (`ui/char_creation_screen.py::_run_creation`)
aceitava qualquer unicode "printable" e defaultava pra literalmente
`"Aventureiro"` quando vazia.

**Regra nova**: 3-16 caracteres, só letras (com acento — jogo é PT-BR),
sem espaço/número/símbolo — `shared/character_names.py::is_valid_name`
(único ponto de verdade, reaproveitado por cliente E servidor, igual
`fog_codec.py`/`messages.py` já fazem pra outras regras compartilhadas).
Unicidade é **GLOBAL** (todas as contas, não só por conta) e
**case-insensitive** (`LOWER(name)=LOWER(?)`) — nameplate/chat/trade
mostram o nome pra todo mundo, então "Juugo" numa conta e "juugo" noutra
colidiriam visualmente do mesmo jeito que colidiriam na mesma conta.
Checagem é feita DENTRO da mesma transação do INSERT
(`_create_character_sync`), nunca só no cliente. `create_character()`
mudou de retornar `bool` pra retornar um motivo (`"ok"` |
`"invalid_name_format"` | `"name_taken"` | `"limit_reached"` |
`"creation_failed"`) — `server/session.py::_handle_create_character`
repassa direto como `CHARACTER_ERROR.reason`.

**Não migramos os nomes duplicados já existentes no banco** (não pedido,
e adicionar uma constraint `UNIQUE` real quebraria com os duplicados
já lá) — a regra vale só pra criações NOVAS a partir de agora.

**Gerador de nome sugerido**: sílaba inicial + consoante opcional no
meio (m/n/s/r/l/d/t, ~35% de chance) + sufixo (`shared/
character_names.py::generate_name_candidate`) — sempre dentro do
formato válido. Protocolo novo: `SUGGEST_NAME` (C→S, `{}`) /
`NAME_SUGGESTION` (S→C, `{name}`) — precisa ir ao servidor porque só ele
sabe quais nomes já existem (`server/auth.py::suggest_character_name`,
até 8 tentativas até achar um livre). Cliente (`ui/char_creation_screen.py`)
pede uma sugestão ao abrir a tela de criação e preenche a caixa
automaticamente; botão "Sortear" pede outra a qualquer momento (trava
enquanto uma sugestão já está a caminho). Modo offline (já removido
deste branch, §30, mas as funções ainda existem) gera localmente sem
round-trip — não tem banco compartilhado pra checar.

**Validado**: `tests/test_character_names.py` (28 testes — formato,
charset, unicidade global e case-insensitive, limite de 3 personagens,
gerador sempre produz nome válido, sugestão nunca repete nome já
existente, wiring fim-a-fim de `CREATE_CHARACTER`/`SUGGEST_NAME` via
`SessionManager` com banco temporário isolado) + `tests/test_session.py`
ajustado (`_valid_char_name` — usernames de teste como "user_ap_test"
viraram nome de personagem inválido; login continua usando o username
cru, só o nome do personagem precisa ser válido). Suíte completa
299/299, rodada 3x.

**Não validado**: teste manual em jogo (criar personagem com nome
inválido/duplicado mostra o erro certo; botão "Sortear" busca nome novo
sem travar a UI).

**Adendo (20/07/2026)**: 1ª letra do nome sempre maiúscula — jogador
pode digitar tudo minúsculo. `shared/character_names.py::normalize_name`
(único ponto de verdade) aplicado em 2 lugares: cliente, a cada tecla
(`ui/char_creation_screen.py::_run_creation`, resto do nome não é
mexido — só a 1ª letra); servidor, antes de validar/gravar
(`_create_character_sync`) — defesa contra um cliente modificado que
mande o nome cru. Validado: `tests/test_character_names.py` (+6 testes:
`normalize_name` isolado + `_create_character_sync` grava capitalizado
mesmo recebendo tudo minúsculo). Suíte completa 305/305, rodada 3x.

**Adendo 2 (20/07/2026) — bug real reportado pelo usuário (print)**:
criar personagem com nome já usado (`CHARACTER_ERROR{reason:
"name_taken"}`) fechava a tela de criação e voltava pra seleção de
personagem, mostrando o erro cru ("Erro ao criar: name_taken") no
lugar errado — usuário tinha que clicar em "Criar Personagem" nervo
pra tentar de novo, perdendo classe selecionada e sugestão de nome.
Causa: `run_online` mandava `CREATE_CHARACTER` só DEPOIS de
`_run_creation` retornar (já fechada), então a resposta do servidor só
podia ser tratada na tela de seleção. Fix: `_run_creation` agora manda
`CREATE_CHARACTER` no clique de "Confirmar" e ESPERA a resposta
internamente (mesmo padrão já usado pra `SUGGEST_NAME`) — botão vira
"Criando..." (travado) enquanto aguarda; `CHARACTER_ERROR` mostra
mensagem amigável (`_ERROR_MESSAGES`, por `reason`) abaixo da caixa de
nome e mantém a tela aberta pra o jogador corrigir e tentar de novo sem
perder o que já preencheu; só retorna (fecha a tela) em
`CHARACTER_CREATED` de verdade. Ramo `pending_action == "creating"` do
`run_online` (código morto agora) removido. Suíte completa 305/305,
rodada 3x (mudança só no cliente, sem lógica nova testável isolada —
`_run_creation` é um loop de evento pygame sem hooks pra unit test,
mesma lacuna de cobertura que já existia antes desta mudança).

**Não validado**: teste manual em jogo (nome duplicado mantém a tela de
criação aberta com "Nome já escolhido, digite outro."; botão trava em
"Criando..." até a resposta chegar).

---

### §34.29 — Navegação entre telas: ESC na seleção volta ao login + botão "Deslogar" no jogo (20/07/2026)

Pedido do usuário: (1) ESC na tela de seleção de personagem fechava o
jogo inteiro — devia voltar pra tela de login (trocar de conta); (2)
faltava um botão "Deslogar" (além de "Sair do jogo") pra voltar direto
pra seleção de personagem sem fechar o jogo (trocar só de personagem,
mesma conta).

**Achado que evitou inventar protocolo novo**: `ui/char_creation_screen.py::
run_online` já retornava `False` no ESC com a intenção documentada
"usuário voltou ao login" — só que `main.py` nunca completou essa parte
(comentário antigo: "Por ora apenas encerra"). E
`client/network.py::NetworkClient` já tem reconexão + re-login
automático embutido (guarda usuário/client_hash, reconecta com backoff,
reenvia `LOGIN` sozinho) — o mecanismo que "Deslogar" precisa já existe
e é testado em produção (usado hoje pra sobreviver a quedas de conexão).
Decisão: **nem "Deslogar" nem "voltar ao login" preservam a sessão
autoritativa no servidor** — os dois desconectam de verdade
(`net.disconnect()`) e reconectam (uma conta nova via tela de login, ou
a MESMA conta silenciosamente via `_connect_and_login` reaproveitando
usuário/senha já em memória). Isso evita abrir uma frente de protocolo
`LOGOUT` que preserva sessão (despawn gracioso, cancelar
duelo/party/fila de arena ativos etc.) — zero mudança em
`server/session.py`/`server/world_server.py`; o disconnect já aciona
toda a limpeza que esses fluxos já tinham (`end_duels_of`/
`end_parties_of`/`end_matches_of`, despawn) porque é o MESMO caminho de
"caiu a conexão" que já existe.

**`main.py`**: reescrito como laço de 2 níveis — `while True` (Etapa 1:
login) contendo outro `while True` (Etapa 2/3: seleção ⇄ jogo).
`_connect_and_login(host, port, user, password)` (novo helper) conecta
+ loga sem UI, reaproveitado tanto pelo fast-path `--user`/`--password`
(só na 1ª volta — ESC depois mostra a tela de login de verdade) quanto
pelo "Deslogar". Fluxo: ESC na seleção → `net.disconnect()` → volta ao
topo do laço externo (tela de login). "Deslogar" (`action == "logout"`
vindo de `GameEngine.run()`) → `net.disconnect()` + `_connect_and_login`
com a mesma conta → reseta o display (`pygame.display.set_mode`) →
volta ao topo do laço interno (seleção de personagem).

**`game.py::GameEngine.run()`**: passou a **retornar** `"logout"` ou
`None` em vez de sempre chamar `pygame.quit()` incondicionalmente —
`self._pending_logout` (novo, paralelo a `_pending_quit`) para o loop
principal sem derrubar o pygame quando é logout (main.py precisa dele
vivo pra reabrir a seleção de personagem na mesma janela).

**`client/menu_handlers.py`**: botão "Deslogar" novo no menu de pausa
(`_draw_main_menu`, entre "Voltar ao Spawn" e "Quit") — ação direta
(`"logout"`), sem confirmação (diferente de "Quit", que tem
`quit_confirm`) — reversível/de baixo risco, só troca de personagem.

**Validado**: `py_compile` de todos os arquivos tocados; suíte completa
305/305, rodada 3x (mudança é de navegação entre telas/processo — sem
lógica isolável em unit test; `tests/test_server_entrypoint.py::
test_client_main_py_executa_como_script` continua cobrindo que
`main.py --help` sobe sem quebrar import).

**Não validado**: teste manual em jogo — ESC na seleção de personagem
volta pra tela de login; "Deslogar" no menu de pausa volta pra seleção
de personagem sem fechar o jogo, reconectando com a mesma conta.

**Adendo (20/07/2026) — bug real reportado pelo usuário**: depois de
"Deslogar", a música/ambient da área continuavam tocando por cima da
tela de seleção de personagem. Causa: "Sair do jogo" mata o `pygame`
inteiro (`pygame.quit()`), o que já parava o mixer de graça — "Deslogar"
deliberadamente NÃO chama `pygame.quit()` (precisa do mixer/display
vivos pra reabrir a seleção na mesma janela), mas por isso também não
para música/ambient sozinho. Fix: `GameEngine.run()` chama
`SOUNDS.stop_music()`/`stop_ambient_stingers()`/`stop_ambient()` antes
de retornar `"logout"` — mesmas 3 chamadas que `_load_ambient_zones` já
faz ao trocar de mapa, só que aqui é ao SAIR do jogo em vez de entrar
num mapa novo. Suíte completa 305/305, rodada 3x.

---

### §34.30 — Versionamento do jogo: SemVer via git tag + número exibido na tela de login (20/07/2026)

Decisão do usuário: cada commit relevante ganha uma versão. Descartada a
ideia original (contador plano tipo `0.400 → 0.401`, sem distinguir fix
de feature) a favor de **SemVer** (`MAJOR.MINOR.PATCH`) — PATCH sobe em
correção, MINOR sobe em feature nova, MAJOR fica reservado pra mudança
grande/quebra de compatibilidade.

- `shared/constants.py::GAME_VERSION` (novo, `"0.4.0"`) — único ponto de
  verdade, só cosmético. **Não confundir com `PROTOCOL_VERSION`** (linha
  acima no mesmo arquivo) — esse trava compatibilidade real de
  cliente/servidor; `GAME_VERSION` é só o número mostrado pro jogador.
- `ui/login_screen.py` — mostra `vX.Y.Z` no canto inferior direito da
  tela de login (única tela sempre vista 1x por sessão, antes de entrar
  no jogo).
- **Processo daqui pra frente**: a cada commit que justifique subir a
  versão, atualizar `GAME_VERSION` E criar a tag git correspondente
  (`git tag vX.Y.Z` no commit) — a tag é o registro de verdade (permite
  `git log vA..vB` pra changelog e `git checkout vX.Y.Z` pra voltar num
  ponto), o número na tela é só a vitrine.

Baseline: `v0.4.0` = estado do repo até este ponto (inclui Fase G/Arena
2x2, fixes de Recarregar/quest/None-slot, validação de nome de
personagem, navegação ESC/Deslogar — tudo commitado antes desta
entrada).

---

### §34.31 — Fase G leva 1: 3 bugs reais achados no primeiro teste de verdade com testers (20/07/2026)

Primeiro teste real da Arena 2x2 (build `v0.4.0` mandado aos testers) —
3 problemas reportados de uma vez.

**1. PvP não funcionava dentro da arena ("não consegui atacar")**. Causa
raiz: hostilidade de arena é resolvida por Faction
(`arena_time_a`/`arena_time_b`, ver §34.27) — o SERVIDOR libera dano
correto via `can_engage()`. Mas o payload de `ENTITY_SPAWN` de PLAYER
nunca manda o campo `faction` (só mob manda — `server/world_server.py`
linhas ~613/663/1357 vs ~925/1243) — então o cliente nunca fica sabendo
que o oponente virou hostil, e o resolver PvP client-side
(`game.py::_client_pvp_context`, único ponto que decide clique
direito/SPACE/skill ANTES de mandar pro servidor) só conhecia duelo e
zona, nunca arena — a ação nunca saía do cliente, mesmo com o servidor
pronto pra liberar. Fix: `client/arena_handlers.py` guarda
`_arena_opponents_server` (server_eids dos oponentes, vindo de
`ARENA_MATCH_START.opponents`) e `_client_pvp_context` ganhou um branch
de arena — mesmo padrão do duelo (`_duel_opponent_local_eid`), mas
resolvendo o server_eid do ALVO na hora do check (via
`_local_eid_to_server_eid`), não author no momento do match_start —
diferente do duelo, o oponente da arena pode ainda nem estar spawnado
localmente quando `ARENA_MATCH_START` chega (a troca de instância
acontece junto), então pré-resolver pra local eid ali sempre daria -1.

**2. "Reloguei em algum lugar que não era a arena, com outros players em
volta"** (com print). Causa raiz: `server/session.py::on_disconnect`
salvava o personagem (`get_player_save_data` → `save_character`) ANTES
de `end_matches_of` rodar — o save capturava o `map_id` SINTÉTICO da
instância da arena (só existe em memória, nunca em disco, ver
`_load_instance`) + o tile relativo ao spawn da arena (só 4 possíveis:
`_SPAWN_TEAM_A`/`_SPAWN_TEAM_B`). No próximo login, `spawn_player` não
reconhece mais aquele `map_id` (instância já descarregada — `if
saved_map_id not in self._map_bundles: saved_map_id = self._map_file`)
e cai no mapa principal, mas MANTÉM o tile da arena — o jogador
aparecia num tile do mapa principal que não tem nada a ver com onde
estava antes, e como só existem 4 tiles de spawn de arena, qualquer
outro tester que tivesse passado pela mesma partida (ou qualquer
partida — os specs são fixos, não por instância) colidia no mesmo
lugar. Fix: `end_matches_of` movido pra ANTES do save em
`on_disconnect`; `MatchProcessorMixin` ganhou `_arena_leave_now(match_id,
eid)` — helper compartilhado que restaura mapa/tile/Facção NA HORA e
remove o player do roster do time (não só do set `eliminated`), usado
tanto por `end_matches_of` (desconexão) quanto pelo novo
`request_arena_forfeit` (item 3) — remover do roster evita que
`_end_match`, quando a partida terminar de verdade depois, tente
restaurar/notificar esse `eid` de novo (o que podia teleportá-lo de
volta de onde quer que ele esteja àquela altura — já numa fila nova ou
outra partida).

**3. Faltava um jeito de desistir da arena sem esperar o time inteiro
perder** (pedido do usuário, não bug) — comando de chat `/forfeit` ou
`/ff` (`shared/messages.py::ARENA_FORFEIT`, C→S `{}`) →
`WorldServer.request_arena_forfeit(eid)` → `_arena_leave_now` (mesmo
helper do item 2) → `ARENA_MATCH_END{won:False}` + `ZONE_CHANGE` de
volta, igual fim de partida normal. Se o forfeit esvazia o time
inteiro, a partida termina de verdade e o outro time vence
(`_end_match`).

**Bug menor achado junto**: botão "Fila de Arena 2x2" continuava
aparecendo DENTRO da própria arena (`client/arena_handlers.py::
_arena_queue_button_rect` não checava se o player já estava numa
partida ativa). Fix: novo estado `_arena_in_match` (True em
`ARENA_MATCH_START`, False em `ARENA_MATCH_END`), botão some enquanto
`True`.

**Validado**: `tests/test_arena.py` (+8 testes — forfeit de 1 membro não
termina a partida, restaura mapa/posição/facção na hora, forfeit do
time inteiro termina a partida e o outro vence, forfeit fora de partida
recusa; ordem end_matches_of-antes-do-save provada diretamente:
`get_player_map`/`get_tile_pos`/`get_player_save_data` refletem o
mapa/tile de ORIGEM logo após `end_matches_of`, nunca a instância) +
`tests/test_client_ui.py` (+3 testes do branch de arena em
`_client_pvp_context`, isolando zona/duelo pra provar que é o branch de
arena mesmo liberando/bloqueando). Suíte completa 313/313, rodada 3x.

**Não validado**: nova sessão manual com testers reais — PvP
funcionando dentro da arena, relogin após sair da arena cai no lugar
certo, `/forfeit`/`/ff` funcionando, botão de fila sumindo dentro da
partida.

---

### §34.32 — Ciclo de partida vira 2 fases: eliminado continuava agindo + modal de fim de partida estilo WoW (20/07/2026)

Dois pedidos do usuário após testar `v0.5.0`: (1) bug — "os players
continuam controlando o personagem mesmo após perder morrer"; (2)
feature — modal de fim de partida com nome/dano/vitória-derrota de cada
personagem + botão "Sair da Arena" (equivalente ao do WoW).

**Causa raiz do bug**: `_eliminate_player` só setava `is_immune=True`
(bloqueia DANO em `apply_damage_core`) — mas `CombatState.can_act()`
(gate de auto-attack/skill, `server/combat_processor.py`/
`server/skill_processor.py`) e `can_move()` (gate client-side de
movimento, `ui/systems.py`) só olham pra `is_stunned`, nunca pra
`is_immune`. Fix: `_eliminate_player` agora seta os DOIS
(`is_immune`+`is_stunned`, sem `stun_timer` — `CombatStateSystem._tick_
stun_timer` só limpa `is_stunned` se `stun_timer>0`, então fica travado
até este mixin mesmo limpar).

**Isso expôs um problema maior ao implementar o modal**: o modal
precisa congelar os 4 (vencedores inclusive — ninguém pode continuar
brigando enquanto o placar é mostrado), mas limpar `is_stunned`
incondicionalmente ao restaurar apagaria um stun REAL e coincidente
(ex: Polimorfia ativa bem no instante em que a partida termina). Fix:
`match["arena_locked"]` (novo, set de eids) rastreia QUEM teve
`is_immune`/`is_stunned` setados POR ESTE MIXIN — só esses são limpos
ao sair (`_arena_leave_now`); um `is_stunned` pré-existente e
não-relacionado nunca é tocado.

**Redesenho do ciclo de vida da partida** (o antigo `_end_match`
monolítico — decide + restaura os 4 + descarrega a instância tudo de
uma vez — não dava pra manter o placar visível):
1. **ATIVA** — golpe letal elimina (`_eliminate_player`, como antes,
   agora com `is_stunned` também).
2. **DECIDIDA** (`_finish_match`, novo — substitui `_end_match`) — time
   inteiro eliminado OU esvaziado por forfeit/desconexão. NÃO restaura
   ninguém ainda: congela os 4 (`arena_locked`), calcula
   nome+dano+vitória de cada um (`CharacterStats.name` +
   `damage_by_eid`, novo — ver rastreador de dano abaixo) e manda
   `ARENA_MATCH_RESULT` pros 4 montarem o modal. `match["decided"]` +
   `match["decided_at"]` (timestamp) guardados pro timeout.
3. **Cada player sai quando quiser** — `ARENA_FORFEIT` reaproveitado
   (mesmo comando de desistir no meio da partida — depois de decidida
   só teleporta de volta, não muda mais o resultado) → `_arena_leave_now`
   restaura mapa/tile/Facção/is_immune/is_stunned SÓ DESSE eid e o
   remove do roster do time.
4. **Timeout automático** (`_tick_arena_results_timeout`, novo,
   `ARENA_RESULT_AUTO_LEAVE_S = 15.0`) — força a saída de quem não
   clicou nem desconectou, pra instância nunca ficar presa na memória
   pra sempre.
5. Quando o roster dos dois times esvazia (todo mundo já saiu), a
   instância é descarregada de vez.

**Rastreador de dano**: `engine/core_systems.py` ganhou
`register_damage_tracker`/`_damage_tracker` — mesmo padrão plugável de
`register_lethal_interceptor`, chamado dentro de `apply_damage_core`
sempre que `dmg>0` é aplicado (`killer_eid != -1`), sem precisar que
cada call site passe um callback manualmente (diferente do
`on_damage_dealt` já existente, que é por-chamada). `WorldServer`
registra `MatchProcessorMixin._track_arena_damage` no boot — acumula em
`match["damage_by_eid"]`, no-op fora de qualquer partida.

**Protocolo**: `ARENA_MATCH_RESULT` (S→C,
`{results:[{eid,name,damage,won}]}` — campo `eid` adicionado
21/07/2026, ver correção abaixo) — mandado uma vez quando a partida é
DECIDIDA, pros 4 (não teleporta ninguém). `ARENA_MATCH_END` (já
existia) continua disparando só quando cada player efetivamente SAI
(clique/forfeit/desconexão/timeout), junto do `ZONE_CHANGE` de volta —
papéis agora bem separados (antes os dois aconteciam juntos, no exato
momento da decisão).

**Cliente**: `client/arena_handlers.py` ganhou o modal (`ui/ui_sizes.py::
ARENA_RESULT_W/H`) — título, banner "Vitória!"/"Derrota" (identifica a
própria linha por `eid` do player local, `self._my_eid` — CORRIGIDO
21/07/2026: usava `CharacterStats.name`, mas nomes NÃO são globalmente
únicos — bug real, ver `PROBLEMAS_ARQUITETURA.md`), lista nome+dano
ordenada por dano decrescente, botão "Sair da Arena" que manda
`ARENA_FORFEIT` (mesmo comando do `/forfeit`, zero handler novo no
servidor pra isso). Modal é bloqueante (`_handle_arena_click` verifica
`_arena_result` antes de qualquer outra coisa), fecha sozinho ao
receber `ARENA_MATCH_END` (servidor já confirmou a saída).

**Validado**: `tests/test_arena.py` (33 testes no total, 7 a mais que
antes desta rodada — vários dos antigos reescritos pro ciclo de 2 fases:
dano rastreado corretamente, `ARENA_MATCH_RESULT` com nome/dano/vitória
dos 4, todo mundo congelado `can_act()==can_move()==False` inclusive
vencedores logo após decisão, sair depois de decidida restaura só quem
saiu, último a sair descarrega a instância, timeout automático força
saída, timeout não dispara antes da hora, stun real não-relacionado
nunca é apagado). Suíte completa 324/324, rodada 3x.

**Não validado**: sessão manual com testers reais — eliminado não
consegue mais agir/mover; modal aparece com placar correto ao fim da
partida; "Sair da Arena" funciona; timeout de 15s força saída se
ninguém clicar.

---

### §34.33 — Eliminação na arena vira morte de verdade (fantasma real, sem revive) — §34.32 tinha resolvido só metade (20/07/2026)

Depois de testar `v0.6.0`, o usuário reportou que o eliminado AINDA
conseguia agir: "os players continuam controlando o personagem mesmo
após perder morrer" — e um segundo problema, mais fundo: "ainda são
alvos atacáveis, e eu consigo continuar usando skills deles". Pedido
específico: o eliminado deveria morrer de VERDADE — mesmo fluxo de PvE
(fica fantasma, aparece o modal "Liberar espírito") — só que sem poder
reviver enquanto a partida durar.

**Causa raiz dos dois problemas — a mesma**: is_immune (única coisa que
§34.32 setava pro eliminado) só bloqueia DANO em `apply_damage_core`
(`if cst and cst.is_immune: return "blocked_immune"`) — não impede
SELECIONAR o eliminado como alvo nem CASTAR skill nele (o cast "acerta",
consome cooldown/recurso, só o número de dano vira 0). is_stunned (que
§34.32 ATÉ setava, mas só pro perdedor) bloqueia `can_act()`/`can_move()`
— mas isso é sobre o PRÓPRIO eliminado agir, não sobre ser alvo. Nenhum
dos dois resolve "não pode mais ser selecionado/atacado" — quem já
resolve isso, em QUALQUER lugar do jogo, é `current_hp<=0` (comentário
já existente em `server/respawn_system.py` linha 8: "current_hp==0 já
bloqueia o corpo como alvo/atacante em todo combat_processor/
spell_completion_processor"). Ou seja: a resposta certa pros DOIS
problemas era deixar o golpe MATAR de verdade, não inventar mais um
flag.

**Redesenho**: `_arena_lethal_interceptor` (server/match_processor.py)
passou de "intercepta e força 1 HP" pra um PONTO DE NOTIFICAÇÃO só —
sempre retorna `False` (nunca intercepta), só chama `_eliminate_player`
(que agora só marca o `eliminated` set + checa time-wipe, sem tocar em
`is_immune`/`is_stunned`) antes de deixar `apply_damage_core` seguir o
fluxo normal: `PendingDeath` → `ServerDeathHandler` →
`RespawnMixin._handle_player_death` — o MESMO caminho de qualquer morte
de PvE, sem nenhum código novo nesse trecho.

**3 ajustes pontuais no fluxo de morte pra funcionar dentro de uma
instância de arena** (nenhum deles muda o comportamento de PvE normal):
1. `_handle_release_spirit` (respawn_system.py) normalmente transfere o
   fantasma pro mapa principal (cemitério) quando a morte foi fora dele
   — dentro da arena isso puxaria o player pra fora da instância antes
   da hora. Fix: se `player_eid in self._player_match_id`, fica
   fantasma no PRÓPRIO tile, sem trocar de mapa.
2. `server/session.py::_handle_revive_request` ganhou um early-return:
   `if self.world_server._player_match_id.get(player_eid) is not None:
   return` — não pode reviver dentro da arena (pedido explícito do
   usuário). `RELEASE_SPIRIT` continua liberado normalmente (o modal
   "aparece", como pedido — só o REVIVE em si é bloqueado).
3. `_tick_ghost_states` pula inteiramente quem está em `_player_match_id`
   — sem isso o corpo (sempre "perto" do próprio fantasma dentro da
   instância) disparava o prompt fantasma "Reviver agora?", que não
   levaria a lugar nenhum já que o REVIVE_REQUEST é recusado (também
   evita, por construção, qualquer cenário em que o tile da arena
   coincida com o raio do cemitério do mapa principal).

**Revive só ao SAIR da arena**: `_arena_leave_now` (chamado por
`request_arena_forfeit`/`end_matches_of`/timeout — mesmos gatilhos de
sempre) ganhou um passo novo: se `GhostState.is_dead`, chama
`_revive_player(hp_frac=1.0, at_corpse=False)` na posição JÁ restaurada
(mesmo critério do `_auto_revive_on_disconnect` existente: nunca revive
"in place" perigoso — aqui "in place" já é o mapa/tile de origem,
seguro por definição). Funciona independente de o player ter clicado
"Liberar espírito" ou não — `_revive_player` só exige `GhostState`
presente, não `is_ghost=True`.

**Achado lateral (defesa em profundidade, não pedido explicitamente mas
direto no escopo)**: `CombatState.is_alive` nunca é setado pelo
SERVIDOR (só o cliente mexe nele, pra bloquear a própria UI/input local)
— então `can_act()` sozinho NUNCA detecta morte do lado servidor.
`skill_processor.py` já tinha proteção explícita contra isso
(`GhostState.is_dead`, comentário: "mesmo que o cliente esteja com bug
visual ou tente burlar can_act()") — `combat_processor.py`
(`_process_player_attacks`, auto-attack) NÃO tinha o mesmo check.
Adicionado o mesmo guard lá (mesmo padrão/comentário) — sem isso, um
cliente modificado que reenviasse `AUTO_ATTACK` depois de morrer ainda
conseguiria atacar de verdade no servidor.

**Validado**: `tests/test_arena.py` (38 testes, +9 novos —
`TestArenaRealDeath`: liberar espírito fica dentro da instância,
ghost-tick nunca avança pra quem tá em partida, revive bloqueado via
`SessionManager._handle_revive_request` de verdade (não só a condição),
sair da arena revive quem morreu, forfeit de quem está vivo não mexe em
GhostState; provas diretas de que um "cliente burlado" reenviando
target/attack depois de morto não aplica dano de verdade). 3 testes
antigos reescritos pro novo modelo (golpe letal agora retorna "killed",
não "applied"; vencedor congelado explicitamente, perdedor já morto de
verdade não precisa). Suíte completa 336/336, rodada 3x.

**Não validado**: sessão manual com testers reais — eliminado morre de
verdade (fica fantasma, corpo visível, modal "Liberar espírito"
aparece); não reviver dentro da arena; não conseguir mais ser
selecionado/atacado por skill; ao sair da arena, revive automaticamente
na posição restaurada.

---

### §34.34 — Fase G leva 1: aceite de partida + contagem regressiva de preparo (21/07/2026)

Pedido do usuário: em vez de entrar direto na arena assim que a fila
pareia, cada um dos 4 deveria ver uma janela "Partida encontrada!" com
botão Aceitar (expira em 10s — quem não aceita simplesmente não entra);
assim que pelo menos um dos 4 realmente entrar, uma contagem regressiva
de 10s (da PARTIDA, não por-jogador — quem entra depois já vê o tempo
restante) trava ação/movimento até liberar o combate.

**Redesenho do ciclo de vida** (0 novo passo antes de ATIVA — ver
docstring de `server/match_processor.py`): `_tick_arena_queue` não
chama mais `_create_match` (removido) — chama `_propose_match`, que
cria o `match_id` em `_active_matches` com `team_a`/`team_b` VAZIOS
(`invited_a`/`invited_b` guardam quem foi chamado) e `instance_key=None`
(mapa só carrega no primeiro aceite — evita alocar instância pra
ninguém). Cada player manda `ARENA_MATCH_ACCEPT` (novo,
`request_arena_accept`) e entra IMEDIATAMENTE, sozinho, sem esperar o
resto — `team_a`/`team_b` crescem incrementalmente. Isso só foi seguro
fazer porque a exploração confirmou que `_arena_leave_now` JÁ mutava
esses rosters depois da criação e reavaliava a condição de vitória
(`if not match[team_key]: ...`) — construir o roster aos poucos reusa
exatamente esse mecanismo, zero mudança em `_eliminate_player`/
`_finish_match`.

**Preparo (`CombatState.is_stunned`)**: primeiro aceite de QUALQUER um
dos 4 seta `match["countdown_deadline"] = now + ARENA_COUNTDOWN_S`
(`shared/constants.py`); cada aceite calcula `remaining =
countdown_deadline - now` e manda no próprio `ARENA_MATCH_START` (campo
novo `countdown_remaining`) — quem entra depois só recebe o tempo
restante, nunca reinicia. Cada entrante ganha `is_stunned=True` +
entra num set próprio `countdown_locked` (distinto de `arena_locked`,
usado só pelo freeze de FIM de partida — momentos diferentes do ciclo,
não podem compartilhar o mesmo set sem um interferir na limpeza do
outro). `_tick_arena_pending` (novo, chamado logo depois de
`_tick_arena_results_timeout`) libera `is_stunned` de quem está em
`countdown_locked` quando `countdown_deadline` vence, e nunca mexe em
quem já foi travado por outro motivo. `_arena_leave_now` (forfeit no
meio do preparo) também limpa `is_stunned` se o eid estiver em
`countdown_locked` — sem isso, quem desistisse ANTES do preparo acabar
ficaria travado pra sempre depois de voltar pro mapa aberto.

**Janela de aceite vencida**: mesma `_tick_arena_pending`, segunda
varredura — se `accept_deadline` (10s a partir da PROPOSTA, distinto de
`countdown_deadline`) vence e ainda não foi varrida, quem não aceitou é
descartado de `_pending_arena_invite`; se os dois times ficaram vazios
(ninguém topou), a partida é só descartada (instância nunca chegou a
carregar); se só um lado tem gente, o outro vence por W.O. (reusa
`_finish_match` sem nenhuma mudança); se os dois têm gente (só
desbalanceado — ex: só 1 de um lado aceitou), segue pro combate
normalmente — times desbalanceados são esperados e aceitos pelo
usuário nesse cenário.

**Protocolo**: `ARENA_MATCH_FOUND` (S→C, `{teammates, opponents}` —
pareou, aguardando aceite), `ARENA_MATCH_ACCEPT` (C→S, `{}`),
`ARENA_COUNTDOWN` (S→C, `{remaining}` — mandado junto do
`ARENA_MATCH_START` de cada entrante). `ARENA_MATCH_START` continua com
o mesmo payload, só muda o GATILHO (por-player, no aceite, não mais em
lote na criação).

**Cliente**: `client/arena_handlers.py` ganhou a janela de aceite
(`_draw_arena_accept_modal`, mesmo padrão do modal de resultado — painel
+ botão, mas fecha SOZINHO ao vencer um prazo local, sem round-trip;
inspirado no único padrão de deadline já existente no cliente,
`client/online_mode_handlers.py::_process_bdf_pending`) e o overlay de
contagem (`_draw_arena_countdown_overlay`, número grande centralizado,
puramente cosmético — quem trava de verdade é o servidor via
`CombatState.is_stunned`, mesmo mecanismo já usado pelo freeze de fim
de partida, zero replicação nova precisou ser escrita). Botão "Fila de
Arena 2x2" também some durante a janela de aceite (mesmo padrão de
`_arena_in_match`).

**Validado**: `tests/test_arena.py::TestArenaAceiteContagem` (9 testes
novos — pareamento não teleporta/atribui facção, gera 4 eventos
`ARENA_MATCH_FOUND`, aceite teleporta só quem aceitou, `ARENA_MATCH_START`
carrega `countdown_remaining` cheio no primeiro aceite e menor num
aceite tardio, time cujo parceiro nunca aceita segue desbalanceado, time
inteiro no-show perde por W.O., ninguém aceita descarta a partida, fim
do preparo libera `can_act()`/`can_move()`). Helper compartilhado
`_queue_and_pair` (usado por quase toda a suíte de arena já existente)
atualizado pra aceitar automaticamente pelos 4 — preserva "entrada
imediata" pros testes antigos sem reescrever cada um. 2 testes antigos
ajustados: `test_fila_pareia_fifo_...` (checava `_player_match_id`
direto após o pareamento — agora é `_pending_arena_invite`, já que
`_player_match_id` só existe depois do aceite) e
`test_forfeit_voluntario_nao_limpa_stun_real_nao_relacionado` (precisa
simular o preparo já ter acabado antes de testar um stun REAL não
relacionado, já que agora todo entrante nasce com `is_stunned=True` do
próprio preparo). Suíte completa 361/361, rodada 3x.

**Não validado**: sessão manual com 4 clientes reais — janela de aceite
aparece pros 4, quem não aceita simplesmente não entra e a arena segue
desbalanceada, contagem regressiva trava movimento/ação até zerar e é
igual pra quem entra depois, W.O. automático quando um time inteiro não
aparece.

**Validado parcialmente pelo usuário (21/07/2026)**: janela aparece pros
4, quem não aceita não entra, W.O. automático — confirmados. Reportado
como pendente: um bug aparente onde a janela de aceite do OUTRO membro
do time sumia quando um aceitava, e o pedido de unificar os 2 tempos.

### Revisão (21/07/2026) — janela de aceite + preparo somados em 30s ancorados no pareamento, configurável

Pedido do usuário: "quando um dos membros clica em aceitar a arena,
some a janela de aceitar do outro membro do time, isso quebra toda a
lógica, a janela deve permanecer na tela e ambos os membros tem que
poder aceitar... unindo os 2 tempos, será 30 segundos para iniciar a
arena a partir do momento que a arena chamou, esse tempo deve estar em
algum lugar configurável."

**Investigação do bug "janela do outro some"**: `ARENA_MATCH_FOUND` e
`ARENA_MATCH_START` (`server/session.py`) são mandados SEMPRE só pra
sessão de quem gerou o evento (`_amf_sess`/`_am_sess`, resolvidos pelo
eid específico) — nenhum broadcast pro time. `_arena_pending_match_val`
(cliente) só é tocado em 4 pontos, todos dentro de
`client/arena_handlers.py`, nenhum dependente de estado de outro
jogador. **Não foi encontrada causa de código** pra um aceite fechar a
janela do OUTRO membro — hipótese mais provável: a janela era de só
10s (curta pra 2 testers coordenarem "clica agora"), e a do segundo
membro pode ter expirado SOZINHA (fecha automaticamente ao chegar a 0,
sem avisar o servidor) quase no mesmo instante do clique do primeiro,
parecendo causa-efeito por coincidência. Não confirmado — pendente de
retest com a janela maior (15s) abaixo; se persistir, precisa de mais
detalhe (o personagem do outro chegou a ser teleportado, ou só a janela
sumiu visualmente enquanto ele continuava fora da arena?).

**Redesenho da contagem**: `ARENA_ACCEPT_WINDOW_S`/`ARENA_COUNTDOWN_S`
(`shared/constants.py`) sobem de 10.0/10.0 pra **15.0/15.0** (30s
total) — ambos independentemente configuráveis pra aumentar em
produção. Mais importante: os dois prazos passam a ser ANCORADOS no
momento em que `_propose_match` pareia a fila, nunca mais recalculados
a partir de quando alguém aceita. Antes, `countdown_deadline` só era
setado no PRIMEIRO aceite (`if match["countdown_deadline"] is None:
... = now + ARENA_COUNTDOWN_S`) — o tempo total do chamado até o
combate liberar variava conforme quando cada um entrava (podia ser só
`ARENA_COUNTDOWN_S` se alguém aceitasse na hora). Agora
`_propose_match` já grava `countdown_deadline = propose_time +
ARENA_ACCEPT_WINDOW_S + ARENA_COUNTDOWN_S` (fixo); `request_arena_accept`
só calcula `remaining` a partir desse valor já existente — satisfaz
literalmente o pedido ("unindo os 2 tempos, 30 segundos a partir do
momento que a arena chamou"), e "quem entra depois vê o tempo certo"
passa a ser garantido por construção (mesmo timestamp fixo pra todo
mundo, não um relativo a quando cada um entrou).

**Validado**: `tests/test_arena.py::TestArenaAceiteContagem` (2 testes
ajustados pro novo valor — `countdown_remaining` logo após o
pareamento = `ARENA_ACCEPT_WINDOW_S + ARENA_COUNTDOWN_S`, não mais só
`ARENA_COUNTDOWN_S`). Suíte completa 390/390, rodada 3x.

**Não validado**: sessão manual com 4 clientes reais — os 2 membros de
um time conseguem ver a janela e aceitar independentemente, sem a
janela de um interferir na do outro; tempo total do chamado até o
combate liberar é sempre 30s.

### §34.34.1 — Causa raiz de verdade do "arena só chama quando alguém se mexe": `has_pending` (session.py) nunca olhava os buffers de evento de arena (22/07/2026)

Depois da revisão acima, usuário testou de novo com servidor reiniciado
e 4 contas reais (2 numa rede diferente) e reportou 3 sintomas juntos:
"só chamou a arena quando movi o personagem, como se pra atualizar a
fila o personagem precisasse se mover"; "cliquei em aceitar nas 4 telas
e ninguém entrou"; "o botão de entrar na fila mostrou de novo" — ou
seja, a fila pareava e o aceite era processado no servidor (teleporte
já acontecia), mas os eventos nunca chegavam ao cliente a menos que
ALGUMA OUTRA coisa acontecesse no mesmo tick.

**Causa raiz**: `SessionManager._on_tick(tick_count, deltas)`
(`server/session.py`) só chama `_dispatch_tick_deltas` (o que de fato
serializa e manda os pacotes) se `has_pending` for `True` — um cheque
manual que soma `any(deltas.values())` (o dict devolvido por
`_collect_deltas()`, que NUNCA carrega nada de arena) mais uma lista
fixa de outros buffers (`_skill_results_this_tick`,
`_pending_loot_notifications`, `_pending_sound_events`, etc.). Os 4
buffers de evento de arena (`_arena_match_found_events_this_tick`,
`_arena_match_start_events_this_tick`, `_arena_match_end_events_this_tick`,
`_arena_match_result_events_this_tick`, todos em `server/world_server.py`,
populados por `server/match_processor.py`) nunca entravam nessa lista —
são consumidos só DENTRO de `_dispatch_tick_deltas` via
`consume_arena_match_*_events()`, então sem essa checagem eles não
tinham NENHUMA influência sobre `has_pending`. Resultado: se nenhuma
outra atividade acontecesse no mesmo tick (ninguém se movendo/
atacando/etc — cenário realista quando 4 jogadores só estão parados
esperando a fila), o early-return descartava o dispatch inteiro e o
evento ficava PRESO no buffer até QUALQUER outra atividade não
relacionada (ex: alguém andar) finalmente disparar `deltas["moved"]`
não-vazio — o que também explica o aceite "não fazer nada"
(`ARENA_MATCH_START`, que carrega a confirmação de um teleporte JÁ
concluído no servidor, ficava preso do mesmo jeito) e o botão de fila
reaparecendo (o cliente nunca recebeu o evento que deveria escondê-lo).

**Fix**: `has_pending` em `_on_tick` ganhou os 4 buffers de arena na
composição do OR. Nenhuma mudança de fluxo/protocolo — só fecha o
buraco de gating.

**Nota pra próximas features**: esta é uma CLASSE de bug — qualquer
sistema novo que acumule seu próprio buffer `_..._this_tick` fora de
`deltas` (em vez de estender `_collect_deltas()`) precisa ser
adicionado manualmente a este `has_pending`, ou fica sujeito ao mesmo
buraco (evento correto no servidor, preso até atividade alheia
"empurrar" o dispatch).

**Validado**: `tests/test_session.py::TestArenaDispatchSemMovimento` (2
testes novos) — chama só `_tick_arena_queue()`/`request_arena_accept()`
diretamente (nunca o `_tick()` inteiro, que rodaria
SpawnZoneSystem/EnemyAISystem/regen contra o banco real de dev e
mascararia o teste com atividade alheia — já observado um falso
positivo assim durante a escrita do teste, corrigido isolando a chamada
e zerando os outros buffers manualmente via `_clear_unrelated_buffers`)
e confirma que `ARENA_MATCH_FOUND`/`ARENA_MATCH_START`+`ZONE_CHANGE`
chegam mesmo com todo o resto do mundo silencioso. Confirmado por
reversão controlada (`git stash` do fix): os 2 testes FALHAM sem o fix
e PASSAM com ele. Suíte completa 392/392, rodada 3x.

**Não validado**: sessão manual com 4 clientes reais, servidor
reiniciado — fila pareia mesmo com todo mundo parado; aceite entra na
arena imediatamente pros 4; botão de fila não reaparece incorretamente.
Também pendente (não confirmado nem descartado): se o "janela do outro
membro some" reportado em §34.34 era na verdade este MESMO bug
(`ARENA_MATCH_START` do primeiro aceite ficando preso e só chegando
tarde/nunca ao segundo membro) — vale re-perguntar se o sintoma some
depois deste fix.

### §34.34.2 — Portão físico (WoW-style) no lugar do freeze de preparo (22/07/2026)

Pedido do usuário: "a ainda coisa que não está funcionando como eu quero,
é que os personagens estão podendo se mover antes da contagem acabar, e
aproveitando isso, quero que no lugar de bloquear as ações dos
personagens, a gente crie um local no mapa, que fique fechado até a
contagem acabar, exatamente como no wow" — anexou `maps/arena_poco_negro.png`
(27×35px, 1px=1tile), já usando as cores REAIS de autoria de tile do jogo
(confirmado por leitura de pixel: `(200,200,200)`=piso, `(120,120,140)`=
parede, `(255,0,0)`=portão, ainda inexistente em qualquer paleta).

**Mudança de comportamento**: preparo deixa de travar ação/movimento
(`CombatState.is_stunned`/`countdown_locked`, removidos de
`request_arena_accept`/`_tick_arena_pending`/`_arena_leave_now`,
`server/match_processor.py`) — cada time fica livre pra se mexer/usar
skill DENTRO da própria sala de espera. Contenção passa a ser FÍSICA: 2
salas de espera (topo/base do novo mapa) ligadas à arena circular central
por 2 segmentos de portão sólido de 3 tiles (`ARENA_GATE_TILES`,
`shared/constants.py` — fonte única compartilhada entre servidor e
cliente), que abrem sozinhos quando `countdown_deadline` vence (mesmo
instante em que `fight_started` vira `True`) — os 2 segmentos juntos, sem
vantagem pra ninguém. A distância real entre as 2 salas (~25+ tiles,
portão sólido no meio) já deixa qualquer skill de range fora de alcance
antes do portão abrir, sem precisar de nenhuma outra trava.

**Novo tile de portão**: `ARENA_GATE_TILE` (`engine/tileset.py`, char `"D"`
em `TILE_PALETTE`/`TILE_MAPPING`) — sólido, `vision_height=0` (não bloqueia
FOV, dá pra ver o time adversário pela "grade" antes do portão abrir).
Abrir = trocar a REFERÊNCIA da célula por `STONE_FLOOR` (nunca mutar o
`TileType` em si — é singleton reusado em toda instância de arena
concorrente). `Tilemap.tile_matrix` é lido AO VIVO por
`is_tile_walkable`/`find_path` (`engine/world_systems.py`) a cada chamada,
sem cache pra invalidar, e cada instância de arena já tem seu próprio
`_MapBundle`/`Tilemap` isolado (`_load_instance`) — trocar a célula nunca
vaza pra outra partida rodando ao mesmo tempo.

**Novo mapa**: `maps/arena_poco_negro.csv` (`ARENA_TEMPLATE_2V2`, trocou de
`arena_2v2.csv`) — gerado 1:1 do PNG do usuário (sem tolerância de cor, os
3 valores batem EXATO com a paleta). `_SPAWN_TEAM_A`/`_SPAWN_TEAM_B`
(`server/match_processor.py`) movem pras salas de espera.

**Protocolo**: `ARENA_GATE_OPEN` (S→C, `{}`) — mandado a cada um dos 4
quando o portão abre (fim do preparo) OU no aceite de quem entra DEPOIS
do portão já aberto (`request_arena_accept` enfileira o evento na hora
pra esse caso — sem isso o cliente, que acabou de carregar o CSV do disco
com o portão sempre fechado, nunca saberia que já pode atravessar). Novo
buffer `_arena_gate_open_events_this_tick` (`server/world_server.py`) —
**entrou no `has_pending` de `server/session.py` desde o commit** (mesma
classe de bug do §34.34.1, corrigida de saída desta vez).

**Validado**: `tests/test_arena.py` — portão sólido logo após aceitar,
abre (vira passável) pros 2 lados ao mesmo tempo quando o countdown vence,
aceite tardio (depois do portão já aberto) recebe o evento na hora;
referências a `arena_2v2.csv`/coordenadas antigas atualizadas pro novo
mapa. Suíte completa 393/393, rodada 3x.

**Não validado**: sessão manual com 4 clientes reais — cada time se move/
usa skill livremente na própria sala fechada mas não atravessa o portão;
portão abre sozinho ao fim da contagem pros 2 lados ao mesmo tempo; quem
aceita bem no fim (depois do portão já aberto pros outros) já entra
vendo o portão aberto.

### §34.34.3 — Portão fica visualmente fechado até o jogador sair/voltar da tela; grupo idem no HUD (22/07/2026)

Usuário testou §34.34.2 e reportou 2 sintomas: (1) o portão "funcionou
muito bem" na colisão (dá pra atravessar assim que o countdown zera), mas
o DESENHO continuava mostrando o portão fechado (vermelho) até o jogador
andar pra fora da tela e voltar; (2) HUD de grupo (slots no canto
superior esquerdo) só aparecia/atualizava quando algum player se movia
depois de aceitar um convite — mesmo sintoma de "algo atualiza o mapa,
mas não sei o quê", pedido explícito de nunca mais deixar uma atualização
acontecer "de carona" numa ação sem relação direta (ver regra nova em
`CLAUDE.md`, seção "Atualização coesa ao adicionar sistema novo").

**Causa raiz 1 — cache de render de tile não invalidado**: `TileRenderSystem`
(`ui/systems.py:1553-1691`) desenha em cima de uma `Surface` cacheada
(`self._cache_surf`) indexada pela posição da CÂMERA (`tile_ox/tile_oy`),
não pelo conteúdo do tile — só reconstrói (total ou parcialmente, via
scroll) quando a câmera cruza fronteira de tile entre frames. Uma mutação
direta de `Tilemap.tile_matrix` (como o swap do portão,
`_handle_msg_arena_gate_open`) não passa por nenhum desses gatilhos, então
o cache antigo continua sendo reblitado até o jogador andar o bastante
pra forçar rebuild — "sair da tela e voltar" é exatamente isso. Fix:
`_handle_msg_arena_gate_open` (`client/arena_handlers.py`) chama
`self._tile_render_system.invalidate_cache()` logo após o swap — mesmo
gatilho já usado por toda troca de mapa (`game.py`) e pelo God Mode
(`ui/god_mode.py`), só nunca tinha sido propagado pra esta mutação nova.

**Causa raiz 2 — MESMA classe de bug do §34.34.1, em 4 buffers a mais**:
investigação encontrou que `has_pending` (`server/session.py::_on_tick`)
também não olhava `_party_state_events_this_tick`
(`server/party_processor.py`, populado por `respond_party_invite` —
exatamente o sintoma reportado), nem `_duel_end_events_this_tick`
(`server/duel_processor.py`), `_trade_cancellations_this_tick`
(`server/trade_processor.py`), nem `_skill_position_corrections`
(`server/world_server.py`, Interceptar etc.) — todos os 4 são consumidos
em `_dispatch_tick_deltas` mas nenhum tinha entrada própria em
`has_pending`, sujeitos ao mesmo "preso até atividade alheia" já corrigido
pra arena. Corrigidos os 4 de uma vez (mesmo commit), não só o de grupo.

**Validado**: `tests/test_session.py::TestPartyDispatchSemMovimento` (novo,
mesmo padrão de `TestArenaDispatchSemMovimento` — chama
`respond_party_invite` direto, nunca o `_tick()` inteiro) — confirmado por
reversão controlada (`git stash`): falha sem o fix, passa com ele. Suíte
completa 394/394, rodada 3x.

**Validado pelo usuário (22/07/2026)**: arena confirmada — portão abre
visualmente na hora certa, sem precisar sair/voltar da tela. Grupo/duelo
também confirmados (HUD/estado atualiza no instante certo).

**Não validado**: trade/Interceptar (os outros 2 buffers corrigidos) não
foram especificamente re-testados manualmente neste ciclo — mesma causa
raiz, mesma classe de fix, risco baixo.

### §34.34.4 — TAB e ESPAÇO nunca listavam oponente de PvP como alvo (22/07/2026)

Usuário testou duelo real e reportou: selecionar o oponente com TAB não
funciona, iniciar auto-attack com ESPAÇO contra ele não funciona, e a
skill "Punho no Queixo" (guerreiro) "não funciona" contra ele.

**Causa raiz (TAB + ESPAÇO)**: `_visible_enemies_sorted`
(`ui/systems.py`, usada por `_cycle_tab_target`/TAB) e `_space_engage`
(`ui/systems.py`, ligado a `K_SPACE`) só iteravam entidades com o
componente `Enemy` (mobs) — nunca `RemoteControlled` (proxy client-side
de outro player). Diferente do clique direito
(`_remote_player_at_world_pos`, já suportava PvP desde antes), essas
duas vias de seleção simplesmente nunca listavam um oponente de duelo/
arena/zona como candidato — `combat_state.target_entity_id` nunca virava
o eid do oponente por esses dois caminhos.

**Por que isso também quebrava skills** (não só auto-attack): a real
sincronização de alvo com o servidor
(`client/remote_entity_handlers.py::_sync_combat_target`, chamada TODO
frame) já resolvia `RemoteControlled.server_eid` corretamente e mandava
`AUTO_ATTACK` — o mecanismo em si estava certo, só nunca recebia um alvo
remoto pra sincronizar via TAB/ESPAÇO. Já skills como Punho no Queixo
dependem de `combat_state.target_entity_id` estar setado (via
`_resolve_target`) para resolver o alvo antes de mandar `CAST_SKILL` — se
o jogador só tentou TAB (nunca setou o alvo de verdade), a skill falhava
com "Nenhum alvo" antes de qualquer coisa sair do cliente. Não foi
encontrado bug adicional na skill em si: `_skill_punho_no_queixo`
(`ui/skill_handlers.py`) já usa `_target_alive`/`_resolve_target`
corretamente, e a execução real acontece só no SERVIDOR
(`server/skill_processor.py`) — o `deal_damage` client-side dentro do
handler nunca roda em modo online pra skills não-AoE (confirmado via
`_use_skill_visual_only`), então a preocupação de "CombatStats ausente
no cliente" ali é irrelevante na prática.

**Fix**: `_visible_enemies_sorted` e `_space_engage` ganham um segundo
loop sobre `RemoteControlled`, incluindo o eid só se
`can_engage(world, player, eid)` retornar `True` (mesmo gate de
`_client_pvp_context` já usado pelo clique — duelo/arena/zona, nunca
players comuns fora de contexto PvP).

**Validado**: `tests/test_client_ui.py` (3 testes novos) — TAB inclui
oponente engajável e NÃO inclui player remoto fora de contexto PvP;
ESPAÇO seleciona/persegue oponente engajável. Confirmado por reversão
controlada (`git stash`): os 2 testes de "inclui" falham sem o fix e
passam com ele. Suíte completa 397/397, rodada 3x.

**Validado pelo usuário (22/07/2026)**: TAB seleciona o oponente de
duelo; ESPAÇO inicia combate contra ele; skills (Punho no Queixo
incluso) funcionam contra alvo player — confirma a hipótese de que a
skill em si nunca teve bug, só nunca recebia um alvo válido.

---

### §34.35 — NPCs de serviço (mercador/ferreiro/treinador/dador-de-missão) ganham HP + combate genérico — Fase 1 (21/07/2026)

Pedido do usuário: "os NPCs de treinamento, comerciantes etc. estão só
com o rótulo de level sem a barra de HP, o correto é também ter o HP...
quero que todos os NPCs, tenham como default, o mesmo que foi feito
para o Guarda Real, todos eles podem combater inimigos hostis... o
treinador do guerreiro luta como guerreiro, o de mago como mago, o de
arqueiro como arqueiro." Explicitamente combinado como **Fase 1**: só
HP + combate GENÉRICO (auto-attack, mesma IA de qualquer mob) — uso das
skills reais do jogador (Interceptar, Bola de Fogo, etc.) fica pra uma
Fase 2 a discutir depois, porque essa IA de rotação de skill não existe
em lugar nenhum do jogo hoje (nem o próprio Guarda Real faz isso).

**Causa raiz do gap**: diferente do Guarda Real (100% servidor desde a
Fase 4, `create_combat_npc`/`_build_combat_entity`), mercador/ferreiro/
treinador/dador-de-missão eram **puramente client-side** — cada cliente
lia o mesmo `{mapa}_entities.json` e criava a própria cópia
independentemente (`game.py::_spawn_entities_from`), sem nenhuma
mensagem de rede envolvida. O servidor só criava um bloqueador mínimo
(`TileMovement`+`NPC()` vazio, `_create_npc_blockers`) na mesma posição,
só pra `EnemyAISystem`/pathfinding não atravessar o NPC.

**Redesenho — essas 4 viram entidades sincronizadas de verdade**:
1. `content/mob_definitions.py` ganhou 3 moldes genéricos em
   `MOB_TABLE` — "Guerreiro (NPC)" (`entity_class: "Warrior"`), "Arqueiro
   (NPC)" (`"Hunter"`), "Mago (NPC)" (`"Mage"`, sem som — mesmo estado de
   QUALQUER mob caster já existente no jogo, Vampiro/Dragão também usam
   `_NO_SOUNDS`, não é uma lacuna nova). Achado que motivou moldes
   registrados em vez de só passar `entity_class` solto:
   `_build_combat_entity` (`engine/entity_factory.py`), quando a `race`
   não está em `MOB_TABLE`, IGNORA o `entity_class` passado e força
   "Guerreiro"/"Arqueiro" (nunca "Mago") baseado só em `is_ranged` — e
   sem `mob_def`, `MobSounds` fica vazio. Só uma raça REGISTRADA dá
   `entity_class` correto + som real.
2. `engine/entity_factory.py::create_merchant/create_blacksmith/
   create_quest_giver/create_trainer` passam a chamar
   `_build_combat_entity` (mesma função que `create_combat_npc` usa)
   ANTES de anexar o componente de capacidade de sempre
   (`Merchant`/`Blacksmith`/`QuestGiver`/`Trainer`+`NPC`) —
   `create_trainer` deriva a raça-molde do `class_id`
   (`_service_npc_race_for_class`: guerreiro→"Guerreiro (NPC)",
   mago→"Mago (NPC)", arqueiro→"Arqueiro (NPC)" — literalmente o pedido
   do usuário); os outros 3 (sem classe inerente) usam
   "Guerreiro (NPC)" como default. Facção nova `"civis"`
   (`content/faction_data.py` — amigável a jogadores, hostil a
   monstro/bandido, mesma relação de `guardas_vila`, mas semântica
   própria pra não confundir "NPC de serviço" com "guarda de vila de
   verdade").
3. `server/world_server.py::_create_npc_blockers` virou
   `_create_service_npcs` — em vez do bloqueador mínimo, chama essas
   4 funções (agora combatentes) de verdade, lendo o MESMO formato
   posicional de `{mapa}_entities.json` que `game.py` já desempacotava.
   `MapLocation` é anexado automaticamente pelo snapshot antes/depois já
   existente em `_load_map_for` (não precisa de código novo pra isso).
4. `_build_mob_spawn_payload` ganhou campos condicionais — só aparecem
   se o componente correspondente existir na entidade: `profession`
   (qualquer `NPC`), `shop_id` (`Merchant`/`Blacksmith`), `is_blacksmith`
   (flag separada, já que `Blacksmith` sempre coexiste com `Merchant` —
   não dava pra usar um "kind" único exclusivo), `class_id` (`Trainer`),
   `quest_ids`/`turn_in_ids` (`QuestGiver`) — `Trainer`+`QuestGiver`
   também podem coexistir na MESMA entidade (treinador com quest), mais
   um motivo pra flags independentes em vez de um enum. Mob normal/
   Guarda Real continuam com payload idêntico a antes (nenhum desses
   componentes existe neles).
5. `client/remote_entity_handlers.py::_spawn_remote_mob` ganhou um bloco
   final que anexa o componente de capacidade certo na entidade
   reconstruída, um por campo condicional presente no payload — a MESMA
   entidade remota (já com `Position`/`Renderable`/`RemoteEntityMeta`/
   `Faction`) passa a também ter `Merchant`/`Trainer`/`QuestGiver`/
   `Blacksmith`. Achado-chave que tornou isso seguro sem tocar em
   NENHUMA UI: `ShopSystem`/`TrainerSystem`/`QuestDialogSystem`
   (`ui/systems.py`, `ui/trainer_system.py`, `ui/quest_system.py`) já
   são 100% agnósticas de como a entidade foi criada — só consultam
   `Position, Renderable, <Componente de capacidade>`. Loja/treino/
   missão continuam funcionando sem nenhuma mudança nesses 3 sistemas.
6. `game.py::_spawn_entities_from` — os 4 loops de criação local
   (merchants/quest_givers/blacksmiths/trainers) entraram pra dentro do
   `if not _online:` que já existia pras outras entidades (enemies/
   spawn_zones) — em modo online, essas 4 chegam pelo spawn de rede
   normal de mob, não mais criadas localmente aqui.

**Resultado**: mercador/ferreiro/treinador/dador-de-missão passam a ter
barra de HP (reusa `_draw_mob_hp_bars`, já genérico por `RemoteEntityMeta`
— zero código novo de render), brigar sozinhos com hostis que entrem no
raio de detecção e voltar pro posto depois (mesma `EnemyAISystem`/leash
que o Guarda Real já tem), e continuar abrindo loja/treino/diálogo de
missão normalmente ao clicar.

**Validado**: `tests/test_service_npcs.py` (13 testes novos — as 4
funções produzem `Combatant`+`CombatStats`+`AIControlled`+
`Faction("civis")` além do componente de capacidade certo; `create_trainer`
com cada `class_id` produz o `entity_class`/`is_ranged` esperado;
NPC de serviço bloqueia dano do player mas recebe dano de mob hostil
normalmente — mesmo padrão de `tests/test_faction.py::
TestCombatNpcArchetype` pro Guarda Real; payload de spawn inclui os
campos condicionais certos só quando o componente existe; mob normal/
Guarda Real continuam com payload idêntico a antes) +
`tests/test_client_ui.py` (3 testes — `_spawn_remote_mob` anexa
`NPC`+`Merchant`/`Trainer` conforme os campos do payload; mob normal
sem esses campos não ganha nenhum componente extra, regressão). Suíte
completa 377/377, rodada 3x.

**Não validado**: sessão manual em jogo — barra de HP aparece sobre
mercador/treinador/ferreiro, um deles briga com um mob hostil próximo e
volta pro posto depois, loja/treino/diálogo de missão continuam abrindo
normalmente ao clicar.

**Fora de escopo desta fase (Fase 2, a discutir depois)**: uso das
skills reais do jogador (Interceptar, Golpe Poderoso, magias, flechas
via sistema de skill) e qualquer IA de rotação/decisão de skill.

### §34.35.1 — 3 bugs achados no primeiro teste de verdade da Fase 1 (21/07/2026)

Feedback do usuário depois de testar em jogo:

1. **Badge de nível duplicado**: `ui/systems.py:1421-1439` tinha um
   desenho de "nameplate de NPC" antigo (badge de nível + nome, sem HP)
   que só se desativava quando a entidade tinha `CombatStats` LOCAL.
   NPC de serviço agora perde `CombatStats` local na reconstrução remota
   (`client/remote_entity_handlers.py::_spawn_remote_mob`, HP vira
   autoritativo via `RemoteEntityMeta` — mesmo tratamento de qualquer
   mob remoto) mas continua com `NPC`, então o guard antigo
   (`not _draw_hp_bar`) passava a achar que "não tinha combate" e
   desenhava o badge velho, AO MESMO TEMPO que o HUD novo
   (`_draw_mob_hp_bars`) desenhava o dele. Fix: guard ganhou
   `and not _is_remote_synced` (checa `RemoteEntityMeta`) — quem
   sincroniza pelo pipeline de mob nunca mais passa por aqui.

2. **Arqueiro (NPC) não lançava flecha visual contra mob hostil**: dano
   acontecia certo (servidor processa tudo igual), mas nenhum projétil
   aparecia. Causa raiz: `client/remote_entity_handlers.py::
   _spawn_mob_projectile` só resolvia `target_seid` contra o player
   local (`self._my_eid`) ou players remotos (`self._remote_players`) —
   nunca contra `self._remote_mobs` (onde mobs E NPCs remotos vivem).
   Um tiro mirando QUALQUER NÃO-player (mob hostil sendo alvo de um NPC
   ranged, ou o inverso) caía no `else: return` e era descartado — bug
   PRÉ-EXISTENTE, nunca antes exercitado porque não havia nenhum NPC
   ranged de combate até esta leva (Guarda Real é melee). Fix: mais um
   `elif target_seid in self._remote_mobs` antes do `return`.

3. **Som de flecha errado**: "Arqueiro (NPC)" reusava
   `attack_ranged: "mob_bow"` (mesmo som genérico de monstro do
   Goblin/Elfo) — usuário pediu o mesmo som do arqueiro JOGADOR.
   `SOUNDS.play_mob_sounds` (`ui/sound_manager.py:469-497`) não tem
   nenhuma distinção NPC-vs-monstro — só toca o que estiver em
   `MobSounds.attack_ranged`, tentando variantes `_1".."_4"` sozinho a
   partir da base. Fix: trocado pra `"arrow_release"` (mesma base do
   som real do arqueiro jogador, `arrow_release_1`/`_2`,
   `client/remote_entity_handlers.py::_spawn_archer_auto_arrow`) — o
   sistema de variantes já existente resolve sozinho, zero código novo.
   `crit` também trocado de `"mob_goblin_crit"` pra `"hit_crit"` (mesmo
   ajuste de "soar mais humano, menos monstro").

Kite (recuo genérico de qualquer combatente `is_ranged=True` quando o
alvo chega perto demais, `EnemyAISystem`) foi CONFIRMADO como
comportamento correto/esperado pelo usuário, não é bug — mantido sem
mudança.

**Validado**: `tests/test_client_ui.py` — 1 teste novo
(`test_spawn_mob_projectile_com_alvo_sendo_mob_remoto`) prova que um
projétil mirando um mob remoto (não-player) agora cria a entidade
visual corretamente. Suíte completa 378/378, rodada 3x.

**Não validado**: sessão manual — flecha visual aparecendo quando
Arqueiro (NPC) atira num mob hostil, som igual ao do jogador, só 1
badge de nível por NPC de serviço.

### §34.35.2 — Causa raiz de verdade do "arqueiro sem flecha/som errado": payload de spawn nunca mandava is_ranged/raça certa pra entidade sem SpawnZone (21/07/2026)

Depois de reportar que a correção de §34.35.1 (item 2/3) "continua
igual", o usuário descreveu o sintoma com mais detalhe: o treinador
arqueiro mata o mob "instantaneamente" (esperado — é level 60 vs mob
level baixo, não é bug), "sem disparar flecha e emite som de attack
melee". Isso apontava pra algo mais fundo que só a config de som.

**Diagnóstico direto** (script isolado com `make_world_server` + tick
loop real): `AIControlled.is_ranged` do treinador fica `True` o tempo
todo NO SERVIDOR (confirmado, dano/IA corretos) — o problema nunca foi
o servidor. O `_build_mob_spawn_payload` (`server/world_server.py`),
porém, mandava pro cliente `race: "Humanoide"` e `is_ranged: False`
pra esse mesmo treinador — os dois ERRADOS.

**Causa raiz**: `_build_mob_spawn_payload` só preenchia `race`/
`is_ranged` de verdade a partir de `SpawnZoneOwner`/`SpawnZone` (mobs
que nascem de zona no mapa). Pra qualquer entidade SEM SpawnZone
(boneco de treino, Guarda Real, e agora NPC de serviço), caía no ramo
`elif ident:`, que:
- Nunca setava `is_ranged` — ficava preso no default `False` do topo
  da função pra sempre. Guarda Real nunca expôs esse bug por ACASO ser
  melee (`is_ranged=False` de verdade) — "Arqueiro (NPC)"/"Mago (NPC)"
  são as primeiras entidades ranged sem SpawnZone a existir.
- Mandava `ident.race`, que guarda a categoria AMPLA ("Humanoide",
  "Fera", etc — só informativa) e NUNCA bate uma chave de
  `MOB_TABLE`. O cliente reconstrói via `create_enemy(race=...)`
  (`client/remote_entity_handlers.py::_spawn_remote_mob`) — sem
  encontrar o `mob_def`, cai no fallback genérico de
  `_build_combat_entity` (`entity_class = "Arqueiro" if is_ranged else
  "Guerreiro"`, NUNCA "Mago", e SEM sons — `MobSounds` fica vazio).
  Combinado com `is_ranged` sempre False vindo do payload, o cliente
  reconstruía SEMPRE como "Guerreiro" melee mudo, não importa o que o
  servidor decidisse de verdade.

**Fix**:
1. `EntityIdentity` (`engine/components.py`) ganhou um campo novo,
   `mob_key` — a chave EXATA de `MOB_TABLE` usada na criação (ex:
   "Arqueiro (NPC)"), diferente de `race` (categoria ampla). Setado em
   `_build_combat_entity` (`engine/entity_factory.py`) a partir do
   `race` já resolvido (antes dele virar a categoria ampla pro campo
   `race` do componente).
2. `_build_mob_spawn_payload`: `race = ident.mob_key or ident.race or
   race` (prioriza a chave específica); `is_ranged` agora SEMPRE lido
   de `AIControlled.is_ranged` da própria entidade (`ai` já era
   buscado no topo da função, só nunca era usado pra isso) — fonte
   única, nunca muda depois da criação, substitui completamente a
   derivação frágil via SpawnZone/EntityIdentity que nunca cobria o
   caso sem zona.

**Validado**: `tests/test_service_npcs.py::
test_payload_de_trainer_arqueiro_manda_race_e_is_ranged_corretos` —
payload de um treinador arqueiro manda `race="Arqueiro (NPC)"`,
`is_ranged=True`, `entity_class="Hunter"` (antes: "Humanoide"/False).
Suíte completa 379/379, rodada 3x.

**Não validado**: sessão manual — Arqueiro (NPC)/treinador de arqueiro
atira flecha visual de verdade com o som certo ao brigar com um mob
hostil (não mais melee/mudo).

### §34.35.3 — As causas de verdade eram no CLIENTE: entrega da flecha + sons hardcodados; MobSounds vira NpcSounds (21/07/2026)

Depois de §34.35.2, o usuário reportou que continuava tudo errado (som
de goblin no aggro, impacto de melee, flecha invisível) e pediu
explicitamente: elementos do treinador arqueiro devem espelhar o
ARQUEIRO JOGADOR (flecha visual + "arrow_release" no disparo +
"arrow_impact" no acerto), com um componente configurável pros sons —
e que decisões de design passem por ele, não por defaults meus.

**Diagnóstico definitivo** (script servidor + trace completo do pipeline
de som do cliente): o SERVIDOR sempre esteve 100% certo — o treinador
dispara projéteis de verdade (`is_arrow: True`), spawns de
`mob_projectile` são emitidos, e eventos de combate NPC↔mob existem
(`_pending_mob_attacks`, source="auto"). As 3 causas eram todas do lado
cliente/entrega:

1. **Flecha invisível — causa raiz REAL**: `server/session.py::
   _build_update_for_session` só entregava spawns de `mob_projectile`
   pra sessão de quem era O ALVO (`target_seid == session.entity_id`).
   Alvo mob = nenhuma sessão recebe = flecha invisível pra todo mundo
   (limitação pré-existente: espectador também nunca via flecha de mob
   mirando OUTRO player). Fix: entrega ao dono do alvo OU a qualquer
   sessão com o projétil dentro do AOI (mapa validado pelo mapa do
   ATACANTE — projétil não tem MapLocation).
2. **Som de impacto sempre melee**: o ramo "alvo mob" de
   `_apply_combat_result` (client/remote_entity_handlers.py) tocava
   `hit_normal` HARDCODED pra qualquer atacante — nunca consultava o
   componente de sons do atacante (por isso configurar "arrow_release"
   na tabela nunca teve efeito). Fix: novo helper
   `_play_nonplayer_attack_impact` — atacante NÃO-player toca o
   PRÓPRIO som (melee → `attack_melee` no acerto; ranged/caster →
   `attack_impact`, campo NOVO, já que o disparo toca separado);
   fallback `hit_normal` preservado pra atacante player (é o feedback
   dele), atacante sem config (Guarda Real/feras `_NO_SOUNDS`) e
   atacante fora do AOI — zero mudança pra quem já funcionava.
   Detecção ranged/caster usa `EntityIdentity.entity_class` — NUNCA
   `AIControlled`, que é REMOVIDO do espelho remoto na reconstrução.
3. **Som de disparo**: agora toca no momento em que o projétil NASCE
   (`_spawn_mob_projectile` — igual ao arqueiro jogador, que toca
   arrow_release quando a flecha dele nasce), SÓ quando o alvo não é o
   player local (mob→player mantém o fluxo antigo — attack_ranged na
   chegada do COMBAT_RESULT via `_play_attacker_mob_sound` — pra não
   dobrar o som nem mexer no que funciona).

**Renomeação (decisão do usuário)**: `MobSounds` → `NpcSounds`
("todo mob é um NPC, mas nem todo NPC é um mob") — rename mecânico em
todos os arquivos, mesmo componente/campos, + campo novo
`attack_impact`. Fonte de dados continua
`content/mob_definitions.py::MOB_TABLE["sounds"]` (helpers
`play_mob_sounds*` do SoundManager mantêm o nome — resolvem o evento
via getattr, então `attack_impact` funcionou sem mudança neles).

**Dados (decisões do usuário)**: Arqueiro (NPC) = só
`attack_ranged: "arrow_release"` + `attack_impact: "arrow_impact"`
(MESMOS sons do arqueiro jogador); Guerreiro (NPC) = só
`attack_melee: "hit_normal"`; aggro/morte/emotes VAZIOS de propósito em
todos (silêncio — usuário preenche depois na tabela); Mago (NPC) todo
silencioso até a Fase 2. Escopo do fix de som: GENÉRICO (qualquer
atacante não-player), decisão explícita.

**Validado**: `tests/test_service_npcs.py` +2 (sons do arqueiro
espelham player + resto silencioso; guerreiro melee genérico),
`tests/test_client_ui.py` +3 (impacto do NPC ranged toca
`attack_impact`="arrow_impact"; disparo toca `attack_ranged`=
"arrow_release" quando o projétil nasce; atacante sem config cai no
fallback hit_normal antigo — regressão). Suíte completa 384/384,
rodada 3x.

**Não validado**: sessão manual — flecha visível + arrow_release no
disparo + arrow_impact no acerto quando o treinador arqueiro briga com
um mob; nenhum som de goblin restante; goblin→player inalterado.

### §34.35.4 — NPC de serviço volta pro TILE EXATO do spawn após o combate (21/07/2026)

Feedback do usuário: "os npcs precisam voltar para o spawn deles após
acabar o combate". Diagnóstico (script com trainer arqueiro kitando +
mercador melee): o RETURNING já funcionava — o problema é que os 3
pontos de "chegou em casa" do `EnemyAISystem` aceitam Manhattan ≤
`proximity_threshold_tiles` (=1) do spawn. Mob errante, ninguém nota 1
tile de folga; NPC de posto fixo fica visivelmente fora do lugar, e
cada briga desloca de novo (deriva acumulada).

**Fix**: threshold por entidade — novo helper
`EnemyAISystem._settle_threshold_tiles(eid)`: tag `NPC` (NPC de
serviço, Guarda Real) → 0 (tile EXATO); resto → 1 (inalterado). Os 3
call sites (`> self.proximity_threshold_tiles`) trocados pelo helper —
o pathing de RETURNING existente resolve o resto sozinho. Se o tile
exato estiver ocupado (ex: player parado no posto), o NPC espera ao
lado em RETURNING (repath com throttle já existente) e assenta quando
liberar.

**Validado**: `tests/test_service_npcs.py::TestServiceNpcVoltaProSpawnExato`
(2 testes — NPC deslocado 3 tiles volta pro tile exato e assenta IDLE;
mob comum a 1 tile do spawn NÃO se move, regressão do threshold
antigo). Suíte completa 386/386, rodada 3x.

**Não validado**: sessão manual — treinador/mercador voltando pro posto
exato depois de brigar com mob.

### §34.35.5 — Projétil de mob/NPC ranged usa o MESMO visual do jogador (21/07/2026)

Pedido do usuário ("pra finalizar esse tema"): projétil do NPC arqueiro
e de mob ranged igual ao do arqueiro JOGADOR; idem pro projétil do
mago.

**Antes**: projétil de mob era uma entidade `Projectile` primitiva —
flecha = linha reta de 4px de largura orientada pela direção FIXA do
spawn, caster = círculo chapado de 4px; sem rastro, sem rotação por
movimento, sem sprite. O jogador usa `PlayerProjectile`
(`ui/spell_system.py::PlayerProjectileSystem`): flecha com rastro
desbotado de 7 pontos + linha de 20px rotacionada pelo movimento real;
Bola de Fogo é spritesheet animado (10 frames) rotacionado.

**Fix (100% cliente)**: `_spawn_mob_projectile`
(`client/remote_entity_handlers.py`) deixa de criar `Projectile` e cria
um `PlayerProjectile` cosmético — mesmo precedente das flechas de
espectador (`_spawn_bystander_projectile`), com um sentinela NOVO
`target_server_id = -3` em `_on_hit`: totalmente SILENCIOSO (o -2 do
espectador toca som de impacto; aqui os sons já são dirigidos por
`NpcSounds` — disparo no nascimento do projétil, impacto no
COMBAT_RESULT — tocar no projétil dobraria tudo). Mapeamento:
- `is_arrow=True` → `spell_id="arrow"`, 700px/s, cor (101,67,33) —
  IDÊNTICO à flecha do arqueiro jogador (rastro + rotação).
- Atacante `Mage`/`Mago` (via `EntityIdentity` do espelho — nunca
  `AIControlled`, removido do espelho) → `spell_id="bola_de_fogo"`,
  300px/s — IDÊNTICO à Bola de Fogo do mago jogador (sprite animado; o
  timer `_fireball_anim` já avança genericamente pra qualquer
  PlayerProjectile com esse spell_id, zero mudança lá).
- Caster não-mago (Warlock/Vampiro — projétil roxo) ou atacante fora do
  AOI → `spell_id="npc_bolt"` (círculo mágico genérico do pipeline do
  player, raio 6), mantendo cor/velocidade do servidor — preserva a
  identidade visual roxa do Vampiro (decisão conservadora minha,
  sinalizada ao usuário — mudar depois é trocar 1 branch).

O projétil cosmético NÃO é registrado em `_remote_mob_projectiles` de
propósito: ciclo de vida é do `PlayerProjectileSystem` (remove na
colisão visual/fly-out) — registrar faria o despawn do servidor (que
simula o próprio projétil a 380px/s) matar a bola de fogo (300px/s) no
meio do voo.

**Validado**: `tests/test_client_ui.py` (3 — flecha vira
PlayerProjectile spell_id="arrow" 700px/s cor do player; Mago (NPC)
vira "bola_de_fogo" 300px/s; caster não-mago vira "npc_bolt" mantendo
cor/velocidade do servidor). Suíte completa 388/388, rodada 3x.

**Não validado**: sessão manual — flecha com rastro igual à do player
quando o treinador arqueiro atira; bola de fogo animada no lugar do
círculo laranja pra caster Mage; goblin→player com o visual novo
também (mesma rota de spawn).

### §34.35.6 — Sons do Mago (NPC) espelham a Bola de Fogo do jogador (21/07/2026)

Feedback do usuário: "o som do impacto da bola de fogo do mago está
fazendo o som do auto attack melee, tem que arrumar para fazer o som
dele sendo lançado e depois o impacto no mob".

**Causa**: o Mago (NPC) estava com TODOS os campos de som vazios
(decisão anterior "silencioso até a Fase 2") — com `attack_impact`
vazio, `_play_nonplayer_attack_impact` retorna False e o caller cai no
fallback `hit_normal` (som genérico de golpe melee). O lançamento
também era mudo (`attack_magic` vazio).

**Fix — SÓ DADOS** (a arquitetura data-driven de §34.35.3 pagou o
investimento: zero código): `Mago (NPC)` em `MOB_TABLE` ganhou
`attack_magic: "skill_bola_de_fogo_launch"` (toca quando o projétil
NASCE, via o hook de disparo de `_spawn_mob_projectile` — evento
attack_magic escolhido pra classe Mage) e `attack_impact:
"skill_bola_de_fogo_impact"` (toca na chegada do golpe, via
`_play_nonplayer_attack_impact`) — os MESMOS arquivos da Bola de Fogo
do mago jogador (`skill_bola_de_fogo_launch_1.ogg`,
`skill_bola_de_fogo_impact_1..4.ogg`; `play_mob_sounds` já tenta as
variantes `_1..4` sozinho). Aggro/morte/emotes continuam vazios.

**Validado**: `tests/test_service_npcs.py::
test_mago_npc_sons_espelham_bola_de_fogo_do_player` (dados) +
`tests/test_client_ui.py::
test_som_de_lancamento_e_impacto_do_mago_npc_espelham_bola_de_fogo`
(fluxo: launch no spawn do projétil, impact no golpe, sem hit_normal).
Suíte completa 390/390, rodada 3x.

**Não validado**: sessão manual — mago NPC brigando com mob toca launch
ao disparar e impact ao acertar, sem som de melee.

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
| Arena 3x3, campos de batalha 5x5 (torres/bandeira/base), matchmaking solo real, ranking | 🔲 pendente | próximas levas da Fase G |
| Instâncias de dungeon/raid PvE | 🔲 pendente | reaproveita o instanciamento genérico da Fase G (`_load_instance`/`_unload_instance`) |
| Client-side prediction de movimento | 🔲 pendente | `client/` |
| Validação de nome de personagem (formato + unicidade global) + gerador de nome sugerido | ✅ completo, não validado em jogo | `shared/character_names.py`, `server/auth.py`, `client/ui/char_creation_screen.py` |

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

### §34.36 — Leva pós-playtest (22-23/07/2026): Fase A — 7 fixes isolados de baixo risco

Depois de uma sessão grande de testes reais, usuário levantou 11 bugs +
4 pedidos de feature. Plano de ação completo (8 fases, A-H) salvo e
aprovado — ver `C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`
no momento da escrita (fases B em diante ainda pendentes). Fase A = os 7
fixes mais isolados/baixo risco, todos testados e fechados juntos.

**A1 — Chat travava movimento**: `PlayerInputSystem.update` (`ui/systems.py`)
zerava `can_move` por completo com o chat focado — intenção original era só
impedir WASD de mover o personagem enquanto digitava (movimento por
teclado é `pygame.key.get_pressed()`, não `KEYDOWN`), mas a implementação
também travava clique-pra-andar/auto-move/perseguição de combate.
Corrigido: `_chat_blocks_keyboard_move` separado, só suprime o bloco de
movimento manual por teclado; `can_move` fica intocado pro resto (auto-move,
perseguição).

**A2 — Som de auto-attack melee ausente ao acertar outro player**:
`_play_attacker_mob_sound` (`client/remote_entity_handlers.py`, branch
"Player remoto foi atacado") não tinha o guard "atacante sou eu mesmo" que
sua irmã `_play_nonplayer_attack_impact` já tinha — quando
`server_attacker == self._my_eid`, a função devolvia `True` por omissão
(nem `_remote_mobs` nem `_remote_players` contêm o próprio eid),
suprimindo `hit_normal`/`hit_crit` do PRÓPRIO golpe do jogador. Corrigido
com o mesmo guard.

**A3 — Ícones de status ausentes em players remotos**: dados já chegavam
certos (`_sync_mob_effects` já resolve `RemoteControlled` como fallback),
só faltava desenhar — `_draw_mob_hp_bars` tinha o bloco de ícones
(`StatusEffects` + `_build_effects_row` + `add_icon_offset`),
`_draw_remote_players` não. Copiado o bloco.

**A4 — Nameplate completo em player morto**: única checagem de morte em
`_draw_remote_players` era `if _arena_match and rc.hp <= 0: continue` — só
dentro de Arena, e escondia TUDO (nome incluso). Corrigido: `rc.hp<=0` vale
em qualquer contexto, mas agora só pula a barra de HP/badge de nível — o
nome sempre desenha.

**A7 — Barra/nome vermelhos em zona PvP**: `_draw_remote_players` só
considerava duelo/arena pra decidir hostilidade — nunca zona PvP.
Consolidado: troca o cálculo manual por
`self._client_pvp_context(self.world, self.player_entity, local_eid)`
(`game.py`, já cobre duelo OU zona PvP OU arena, já exclui mesmo grupo) —
fonte única com o que já decide clique/skill, em vez de 2 lógicas
paralelas que podiam divergir.

**A5 — "Voltar ao Spawn" não trocava de mapa**: `_handle_unstuck`
(`server/session.py`) só fazia `snap_to_tile` (tile/pixel) — nunca
`MapLocation.map_file`/`_player_maps`, nunca `ZONE_CHANGE`. Quem estava
numa caverna ficava preso lá na coordenada de spawn de `map_1`. Corrigido
com o mesmo padrão de `respawn_system.py::_handle_release_spirit`: se o
mapa atual ≠ mapa principal (e não está numa partida de Arena — mesmo
guard de lá, nunca yankar alguém pra fora de uma instância), chama
`transfer_player(...)` + manda `ZONE_CHANGE` + `known_eids.clear()`.

**A6 — Corpo duplicava ao morrer / um sumia ao liberar espírito**: dois
mecanismos de corpo coexistiam — a entidade real tingida de cadáver
(`_handle_player_death`, já grava `_player_corpses[eid]` no INSTANTE da
morte) e um sweep em `_build_update_for_session` (`server/session.py`,
roda todo tick) que entregava o marcador sintético `player_corpse` sem
checar `GhostState.is_ghost` — disparava já no 1º tick após a morte,
criando um segundo corpo sobreposto. Ao liberar o espírito, a entidade
real é despawnada — some ela, sobra só a sintética. Corrigido: o sweep só
entrega o marcador quando `GhostState.is_ghost == True` (bate com o
propósito original documentado no próprio código: "necessário pra um
ghost ver o próprio corpo ao se aproximar").

**Validado**: suíte completa 401/401 (7 testes novos: A5 ×2 em
`tests/test_session.py::TestUnstuck`, A6 em
`TestPlayerDeathEvent::test_marcador_sintetico_de_corpo_so_aparece_apos_liberar_espirito`
— confirmado por reversão controlada, falha sem o fix/passa com ele; A3/
A4/A7 em `tests/test_client_ui.py`, reescrevendo o fixture de
`_draw_remote_players` pra incluir os mixins de PvP context), rodada 3x.

**Não validado**: sessão manual — os 7 itens precisam de teste real por
um tester (chat não trava mais movimento; auto-attack melee toca som
contra player; ícones de stun/sleep aparecem em player remoto; nameplate
de morto só com nome; zona PvP deixa barra/nome vermelhos; "Voltar ao
Spawn" troca de mapa corretamente saindo de caverna; corpo não duplica
mais ao morrer).

### §34.37 — Leva pós-playtest: Fase B — filtro de aliado em AoE (Canção de
Ninar + Brado Provocativo) e fim da predição local do Interceptar (22-23/07/2026)

**B1 — Canção de Ninar e Brado Provocativo afetavam aliados**: mesma
classe de bug já corrigida em Pirofagia (20/07/2026) — `apply_effect()`
(`engine/core_systems.py`) não tem gate de facção, e as duas skills
iteravam `CombatStats` no raio sem checar `can_engage` antes de aplicar
`"sleep"`/`"enraged"`. Corrigido com o mesmo padrão de Pirofagia:
`can_engage(self.world, self.player_entity_id, eid)` antes de cada
`apply_effect`, em `ui/skill_handlers.py::_skill_cancao_ninar` e
`_skill_brado_provocativo`. O Brado ainda não é um taunt de verdade (isso
é a Fase D) — só parou de afetar aliados.

**B2 — Interceptar em loop de dash contra alvo em movimento**: cliente
prediz o dash otimisticamente no input (`_interceptar_dash_visual`,
antiga, `ui/systems.py`) usando a posição LOCAL do alvo (possivelmente
desatualizada); servidor resolve com a posição LIVE — divergência quase
garantida com alvo em movimento, causando ou dash duplo corrigido em
sequência, ou rejeição total com snap-back repetido (o "loop" relatado).
Corrigido removendo a predição local inteira (`_interceptar_dash_visual`
deletado, era o único call site) — a animação agora só toca quando a
correção confirmada do servidor chega via `ENTITY_MOVE` (`is_dash=True`,
`skill_rejected=False`), reaproveitando o MESMO mecanismo de
reconciliação já usado por qualquer deslocamento forçado por servidor
(knockback etc.): como `player_tm.is_moving` fica `False` sem predição
prévia, o handler entra no branch que INICIA a animação do zero, em vez
de reconciliar algo já em andamento. Rejeição não anima nada (nunca há o
que desfazer). Único trade-off: perde o feel instantâneo de input local
(delay de rede antes do dash aparecer) — exatamente a troca pedida pelo
usuário.

**Descoberta lateral — flakiness pré-existente em `tests/test_server.py`**:
ao rodar a suíte completa 3x pra fechar esta fase, 3 testes diferentes
falharam em rodadas separadas (`TestPlayerAttacksMob::
test_player_attack_reduces_mob_hp`, `TestAutoAttackFlow::
test_player_target_must_reach_server`, `TestPlayerAttacksMob::
test_no_duplicate_despawn`) — nunca isolados, só na suíte completa.
Investigação (revertendo a Fase B via `git stash` e rodando a baseline 3x
limpa, depois restaurando e reproduzindo a falha) confirmou: não é
regressão da Fase B (nenhuma delas toca auto-attack simples contra mob) —
é fragilidade pré-existente. `TestPlayerAttacksMob`/`TestAutoAttackFlow`
davam só 60 ticks (3s ≈ 1-2 tentativas de auto-attack) esperando "pelo
menos 1 hit" — miss/dodge/parry é um resultado LEGÍTIMO do roll, então
"0 hits em 1-2 tentativas" é raro mas não impossível. Como o módulo
`random` do Python é GLOBAL e nunca resetado entre testes, adicionar
QUALQUER teste novo em QUALQUER arquivo (não só os relacionados à Fase B)
muda quantos números aleatórios foram consumidos antes de chegar nesses
testes, deslocando o roll o bastante pra ocasionalmente cair numa
sequência infeliz. **Corrigido** (fora do escopo original da Fase B, mas
a mesma classe de fragilidade ameaçava invalidar o "suíte 3x limpa" de
qualquer fase futura): os 8 `run_ticks(self.ws, 60)` dessas 2 classes
viram `run_ticks(self.ws, 240)` (12s) — margem generosa de tentativas,
sem prender os testes a um seed específico.

**Validado**: `tests/test_combat.py::TestAoeSkillsAllyFilter` (2 testes
novos, Canção de Ninar/Brado Provocativo não afetam aliado, só inimigo —
confirmado por reversão controlada) e `tests/test_client_ui.py` (3 testes
novos — método de predição removido não existe mais; correção confirmada
do servidor anima do zero; rejeição não anima nada). Suíte completa
406/406 (+5 testes novos, +1 pelo bump de tick count não alterar
contagem), rodada 3x limpa (depois do fix de flakiness).

**Não validado**: sessão manual — Interceptar contra alvo em movimento
(sem loop) e parado (dash ainda funciona normalmente); Canção de Ninar em
grupo misto (aliado dorme? não deveria).

### §34.38 — Leva pós-playtest: Fase D — Brado Provocativo vira taunt de
verdade (hard-CC, referência LoL) (23/07/2026)

Item de maior risco/escopo da leva pós-playtest — implementado por
último, isolado, com suíte própria (`tests/test_taunt.py`). Pedido do
usuário, com referência trazida por ele mesmo: taunts hard-CC de LoL
(Rammus/Galio/Shen) — o alvo é forçado a andar até o caster e
autoatacá-lo, sem poder usar habilidades, por um tempo fixo (3s aqui).
Nunca existiu nada parecido no jogo antes (grep confirmou zero menções a
"taunt"/"forced_target"/CC-que-força-movimento).

**Novo efeito `"taunted"`** (`content/status_effects_data.py::EFFECT_DEFS`):
aplicado via o mesmo `apply_effect()` genérico de sleep/stun/etc,
`magnitude` carrega o eid do taunter. Deliberadamente **não** marca
`blocks_move`/`blocks_act` (os 2 flags genéricos que `is_movement_locked`/
`is_action_locked` leem) — `is_action_locked` também é usado como
early-exit do auto-attack SERVER-SIDE (`server/combat_processor.py`), e
o taunt PRECISA que esse auto-attack continue disparando (forçado, contra
o taunter) — marcar `blocks_act=True` bloquearia o próprio mecanismo que
o taunt depende para funcionar. Os 3 bloqueios reais são feitos à mão,
cada um no ponto certo:
1. **Movimento livre do jogador**: `move_player` (`server/world_server.py`)
   ganha `"taunted"` na mesma tupla hardcoded de CC totalmente
   imobilizante (`sleep`/`stun`/`root`/`fear`) — qualquer MOVE
   client-initiated é recusado.
2. **Skill livre do jogador**: `server/skill_processor.py::
   _process_skill_requests` ganha um check dedicado (`StatusEffects.has(
   "taunted")` no CASTER) logo após o `is_action_locked` genérico —
   silent-continue, mesmo padrão de sleep/stun.
3. **Input local (cliente)**: `PlayerInputSystem.update` (`ui/systems.py`)
   ganha um bloco dedicado (não o genérico `is_movement_locked`/
   `is_action_locked`, pela mesma razão do item 1 acima) forçando
   `can_move=False`/`can_act=False` localmente quando `StatusEffects.has(
   "taunted")`.

**`TauntSystem`** (novo, `engine/world_systems.py`, registrado por bundle
de mapa em `server/world_server.py::_load_map_for`, mesmo padrão de
`EnemyAISystem`): pilota o movimento forçado — **só de PLAYERS**. Mobs
NÃO precisam: `_skill_brado_provocativo` já escreve
`AIControlled.state="CHASING"`+`target_eid=taunter` diretamente, e a
RETENÇÃO de alvo já existente em `EnemyAISystem` (mob em estado de
combate mantém o alvo retido enquanto válido, ignorando reavaliação de
`_select_target` — mecanismo pré-existente, não uma peça nova) já
garante o "travado" sem precisar de nenhum guard novo ali — descoberta
feita lendo o código antes de implementar uma trava redundante. Pra
PLAYERS (sem `AIControlled`/`EnemyAISystem` guiando), `TauntSystem` a
cada tick: se adjacente ao taunter (chebyshev ≤ 1), seta
`target_entity_id`/`is_pursuing=True` (o auto-attack genérico de
`combat_processor.py` cuida do resto, zero código novo pra isso); senão,
avança 1 passo de path (reaproveita `PathfindingSystem.find_path` +
`engine.utils.start_tile_movement` — os MESMOS primitivos que
`EnemyAISystem` já usa pra mobs, não reinventados) e registra em
`_moved_this_tick` (players precisam de delta explícito pra sincronizar
com outros clientes; mobs não, têm snapshot próprio). Se o taunter
morrer/ficar inválido no meio do efeito, `TauntSystem` remove
`"taunted"` na hora (mesmo espírito da quebra de polymorph/sleep por
dano em `apply_damage_core`, só que aqui por invalidação do alvo forçado).

**`_skill_brado_provocativo`** (`ui/skill_handlers.py`): filtro de aliado
via `can_engage` (parte da Fase B, ver §34.37) + agora lê
`radius_tiles`/`duration` do `SKILL_CATALOG`
(`content/skill_config.py::"brado_provocativo"`, `duration` corrigido de
10.0 → 3.0 pra bater com o pedido) em vez de hardcodear — mesma regra já
documentada no `CLAUDE.md` pra outras skills ("multiplicador vem SÓ do
SKILL_CATALOG, NUNCA hardcodear no handler"). O antigo efeito `"enraged"`
(+10%/+5% dano, um buff — não fazia sentido pra uma CC que o CASTER
inflige no INIMIGO) foi removido, substituído por `"taunted"`.

**Diagnóstico ao vivo antes de formalizar os testes** (mesma metodologia
já usada nesta sessão pro bug de `has_pending`): rodar contra um mob e um
duelo real revelou 2 problemas que eram do PRÓPRIO SCRIPT de
diagnóstico, não do código — (1) 2 players sem contexto de PvP são
"amigáveis" por padrão (`can_engage` bloqueava corretamente, não é bug);
(2) coordenadas de teste escolhidas ao acaso caíam em área sólida do
mapa (`find_path` retornando `None` corretamente). Corrigido o script
(duelo real via `request_duel`/`respond_duel_invite`, coordenadas
walkable) — aí sim confirmou o fluxo completo funcionando ponta a ponta.

**Validado**: `tests/test_taunt.py` (8 testes novos — mob força CHASING+
status, mob fora do raio não é afetado, player em duelo recebe
`"taunted"` com magnitude certa, `move_player` recusa MOVE livre,
`CAST_SKILL` recusa skill do taunted, `TauntSystem` conduz até adjacente
e autoataca, expira após a duração, libera na hora se o taunter morre) —
confirmado por reversão controlada (`TauntSystem` comentado da lista de
sistemas do bundle): os 2 testes que dependem dele falham sem, passam
com. Suíte completa 414/414, rodada 3x.

**Não validado**: sessão manual — taunt real contra mob (anda até o
guerreiro, autoataca, sem poder castar) e contra player em duelo (mesmo
comportamento, mais o bloqueio de movimento/skill visível no cliente);
medir os 3s exatos; testar quebra por morte do taunter em cenário real
(não só no teste automatizado).

### §34.39 — Leva pós-playtest: Fase E — modal de estatísticas do
personagem (23/07/2026)

Novo componente ECS `CharStatsTracker` (`engine/components.py`), server-
autoritativo, mesma regra de `SkillLevels`/`QuestLog`: `pve_damage`,
`pvp_damage`, `mobs_killed`, `players_killed`, `duel_wins`, `duel_losses`,
`arena_wins`/`arena_losses` (dict por modo — `{"1v1":0,"2v2":0,"3v3":0}`,
"1v1"/"3v3" já existem no schema mesmo sem os modos existirem ainda, ver
Fase H). Persistido em coluna nova `char_stats_json` (`server/auth.py::
init_db()`, migração `ALTER TABLE` — mesmo padrão de `quests_json`/
`skill_levels_json`); carregado/salvo em `server/world_server.py::
spawn_player`/`get_player_save_data` e passado por `server/session.py::
_build_save_merge` (server-autoritativo, nunca client-influenciado).

**Hooks de tracking (nenhum lugar novo — todos já eram o ponto único de
verdade certo para o evento correspondente)**:
- **Dano PvE/PvP**: `register_damage_tracker` (`engine/core_systems.py`) é
  um slot ÚNICO — antes só `MatchProcessorMixin._track_arena_damage`
  (placar de fim de partida) estava registrado. Composto agora em
  `WorldServer._damage_tracker_composite` (mesmo padrão de
  `_lethal_interceptor_composite`, já existente pra duelo+arena): chama
  `_track_arena_damage` E incrementa `pve_damage`/`pvp_damage` do
  `killer_eid`, distinguindo os dois checando se o ALVO tem
  `PlayerControlled` (= é player = PvP; senão = mob = PvE) — mais simples
  que checar `Faction`/zona, e correto por construção (dano
  jogador→jogador só acontece quando `can_engage` já liberou PvP por
  algum contexto — duelo, arena, zona).
- **Mobs mortos**: `server_death_handler.py`, incrementa `mobs_killed` do
  `first_attacker_eid` (mesmo dono do loot/quest kill) quando resolvido.
- **Players mortos (PvP de mundo aberto)**: `server_death_handler.py`,
  bloco "Player morreu (PvP)" — incrementa `players_killed` do
  `pd.killer_entity_id` SE o killer também for player. Só dispara pra
  kills de verdade (zona PvP) — duelo e arena NUNCA chegam a
  `PendingDeath` de player (golpe letal é interceptado, vira 1 HP + fim
  de partida/duelo — ver `_lethal_interceptor_composite`), então não há
  dupla-contagem com `duel_losses`/`arena_losses`.
- **Duelos ganhos/perdidos**: `duel_processor.py::end_duel` — só quando
  `reason=="win"` e há vencedor/perdedor de verdade (não em
  `"distance"`/`"disconnect"`, onde ninguém ganha nem perde).
- **Arenas ganhas/perdidas**: `match_processor.py::_finish_match` — por
  enquanto hardcoded `"2v2"` (único modo que existe; Fase H generaliza
  via `ARENA_MODES`/`match["mode_id"]`). Não credita nada se
  `winner_team_key is None` (partida sem vencedor, ex: os dois times
  esvaziaram por forfeit simultâneo).

**Helpers únicos** `incr_char_stat`/`incr_char_stat_mode`
(`engine/utils.py`, ao lado de `is_action_locked`/`is_movement_locked`) —
no-op silencioso se a entidade não tiver `CharStatsTracker` (mob), fonte
única pra nunca divergir do schema do componente.

**Protocolo**: `CHAR_STATS_REQUEST` (C→S, `{}`) / `CHAR_STATS_DATA` (S→C,
privado) — request/response SOB DEMANDA (`shared/messages.py`), não um
canal contínuo tipo `STATS_UPDATE`: `CharStatsTracker` muda em eventos
raros e o modal só abre ocasionalmente, não vale a pena empurrar a cada
tick. Handler `server/session.py::_handle_char_stats_request` lê direto
do componente vivo (sem round-trip de banco) + deriva `quests_completed`
de `len(QuestLog.completed)` (sem campo próprio, já persistido em
`quests_json`).

**UI**: `ui/char_stats_ui.py` (`CharStatsUI`, novo, tecla **C** —
`config.py::DEFAULTS["menu_keybinds"]["estatisticas"]`, rebindável no
editor de atalhos — `client/hotbar_editor_handlers.py::_MENU_ROWS`) —
painel read-only no padrão de `ui/skill_level_ui.py` (mais próximo por
também não ter interação, diferente de `ui/talent_system.py`). Estado
`self._data` começa `None` ("Carregando...") até `CHAR_STATS_DATA`
chegar — `open()` dispara `CHAR_STATS_REQUEST` via
`client/save_sync_handlers.py::_send_char_stats_request`.

**Validado**: `tests/test_char_stats.py` (10 testes novos) — dano PvE
incrementa `pve_damage` e não `pvp_damage` (e vice-versa); dano bloqueado
por facção (`blocked_friendly`) não incrementa nada; mob morto credita
`mobs_killed` do first-attacker; duelo com vencedor credita
`duel_wins`/`duel_losses`, encerramento por distância não credita
ninguém; kill de verdade em zona PvP credita `players_killed`; fim de
partida de arena credita `arena_wins`/`arena_losses` por modo, sem
vencedor não credita ninguém; `get_player_save_data` inclui `char_stats`
com os valores corretos. Suíte completa 424/424, rodada 3x.

**Não validado**: sessão manual — abrir o modal (tecla C), confirmar que
os números batem com ações reais feitas em sessão (causar dano em mob e
em player, matar mob, ganhar/perder duelo, ganhar/perder arena 2v2,
completar quest) e que sobrevivem a um reload de personagem (logout/
login).

### §34.40 — Leva pós-playtest: Fase F — quest tracker: minimizar +
reordenar por progresso absoluto + limite 3→5 (23/07/2026)

**Limite**: `UI.QUEST_HUD_MAX_VISIBLE` (`ui/ui_sizes.py`) `3` → `5` —
única constante, `QuestSystem.MAX_HUD_QUESTS` já lia de lá.

**Ordenação por progresso ABSOLUTO** (pedido do usuário, não
proporcional): `QuestSystem._sorted_active_items(ql)`
(`ui/quest_system.py`) — `sorted(ql.active.items(), key=lambda kv:
sum(kv[1]), reverse=True)`. Uma quest de objetivo grande (`count=5`) mas
"quase pronta" em valor absoluto (`4/5`) aparece antes de uma pequena
(`count=3`) menos avançada em absoluto (`2/3`), mesmo a segunda tendo
proporção maior (0.8 vs 0.67 seria o oposto se fosse por proporção) —
exatamente o comportamento pedido. `render_hud`/`_build_hud_surf` usam a
MESMA lista ordenada (calculada 1x por frame, fatiada em
`MAX_HUD_QUESTS`) — sem isso, cache-key e conteúdo renderizado
poderiam divergir da ordem.

**Minimizar** (não havia nenhum precedente de painel colapsável no HUD —
design novo): `QuestSystem._tracker_minimized: bool` (default `False`) +
botão pequeno (`-`/`+`, 18×18px) no canto superior direito do tracker.
Minimizado, `_build_hud_surf` desenha só o cabeçalho ("Quests (N)", N =
`len(ql.active)` — TOTAL ativas, não só as mostradas) sem a lista.
Estado é só de sessão (não persistido em `config.json`) — decisão de
manter cirúrgico; se o usuário quiser sobreviver a reinício, é 1 chave
nova em `config.py::DEFAULTS` + save/load, mesmo padrão já usado pra
hotbar (`client/save_sync_handlers.py`).

**Hit-test do botão**: o tracker é HUD permanente (sem `_show_x` de
modal), então o clique é escutado incondicionalmente — `QuestSystem.
handle_tracker_click(event)` chamado em `game.py`, no MESMO `elif` chain
unconditional de `_handle_chat_click`/`_handle_duel_click` (linha ~1582).
Geometria do botão só existe DEPOIS de `render_hud` desenhar (tamanho do
painel varia com o conteúdo) — `QuestSystem._last_hud_rect` guarda o
`Rect` do último frame desenhado, `None` quando não há quests ativas
(handler devolve `False` sem quebrar).

**Validado**: `tests/test_quest_tracker.py` (6 testes novos) —
`MAX_HUD_QUESTS==5`; ordenação por soma absoluta (não proporção);
6 quests ativas → só as 5 de maior soma aparecem (3 casos sem
ambiguidade de empate testados); minimizado produz surf mais baixo que
expandido; clique no botão alterna o estado, clique fora não; sem
quests ativas, `_last_hud_rect` fica `None` e o clique não quebra.
Suíte completa 430/430, rodada 3x.

**Não validado**: sessão manual — abrir com 6+ quests ativas, confirmar
ordem visual e que minimizar/expandir funciona com o mouse de verdade
(hit-test de coordenada de tela real, não só o Rect calculado no teste).

### §34.41 — Leva pós-playtest: Fase G — janela maximizada por padrão
(23/07/2026)

Pedido do usuário: jogo abrir maximizado por padrão, usando o botão
nativo de maximizar da janela (sem exclusive fullscreen). Ambiente
confirmado no início da leva: `py -3.10` roda `pygame-ce 2.5.7` com
`pygame.Window` disponível.

**`pygame.RESIZABLE`** adicionado às 2 chamadas de `pygame.display.
set_mode()` em `game.py` (`__init__` e `_apply_scale`, a que roda ao
mudar a escala de UI no menu de configurações) — **preservando**
`DOUBLEBUF | SCALED` + `vsync=1` (o comentário em `game.py` documenta o
bug de vsync/flip já resolvido especificamente com essas flags,
investigação com o usuário 14/07/2026 — qualquer mudança ali seria
arriscada; `RESIZABLE` é só um flag A MAIS, não mexe nos outros).
`SCALED` combinado com `RESIZABLE` é o par documentado do próprio
pygame-ce pra "resolução lógica fixa (`win_w`/`win_h`, calculada de
`1280×720×scale`) + janela redimensionável de verdade" — o SDL recalcula
sozinho a escala de apresentação (e a tradução de `pygame.mouse.
get_pos()` pro espaço lógico) a cada resize, sem nenhum código adicional
aqui — **não precisou** de handler pra `VIDEORESIZE` nem recriar
`self.screen` a cada resize (diferente do que o plano original cogitava
como possivelmente necessário).

**Maximizar por padrão**: `pygame.Window.from_display_module().
maximize()`, chamado 1x no boot logo após o `set_mode()` inicial (maximiza
o CONTAINER da janela sem mudar a resolução LÓGICA pedida a `set_mode` —
`SCALED` cuida da apresentação) — condicional a `config.json::
window_mode` (novo, `config.py::DEFAULTS["window_mode"]="maximized"`).
Chave nova: `config.load()` mescla `{**DEFAULTS, **data}`, então
jogadores com `config.json` já salvo (sem esta chave) recebem
"maximized" pelo merge — vira o default de TODOS, novos e existentes,
exatamente como pedido, sem precisar de migração explícita. Chamada
envolta em `try/except` — nunca derruba o boot do jogo se falhar num
driver/GPU específico.

**Persistência da preferência do jogador**: `pygame.WINDOWMAXIMIZED`/
`WINDOWRESTORED` adicionados ao `pygame.event.set_allowed()` (antes
filtrados fora — teriam sido descartados silenciosamente sem isso) e
tratados no loop principal de eventos (`game.py::run`, logo após o
`QUIT`): clique no botão nativo de maximizar/restaurar atualiza
`self._window_mode_pref` + `_save_config()` na hora — próximo boot já
abre no estado que o jogador deixou (maximizado OU restaurado/
"windowed"), não sempre forçado a maximizado.

**Risco remanescente**: médio — a nota de vsync/flip no código é um
aviso explícito de fragilidade já sofrida antes, e o comportamento real
de resize/maximize do SDL não pode ser observado neste ambiente
(headless, `SDL_VIDEODRIVER=dummy`). Verificação manual em Windows real
é **obrigatória** antes de considerar esta fase encerrada de verdade.

**Validado** (automatizado, o que dá pra testar sem janela real):
`tests/test_config.py` (3 testes novos) — `DEFAULTS["window_mode"]`
é `"maximized"`; `config.json` existente sem a chave recebe o default
no merge; `window_mode="windowed"` salvo pelo jogador sobrevive a um
load(). Smoke test manual (`SDL_VIDEODRIVER=dummy`) confirmou que
`set_mode` com os 3 flags + `Window.from_display_module().maximize()`
não lança exceção. Suíte completa 433/433, rodada 3x.

**Não validado (requer Windows real, não headless)**: abrir o jogo e
confirmar que inicia maximizado; clicar restaurar/maximizar nativo e
medir se o delay de flip/vsync documentado nos comentários NÃO volta;
redimensionar a janela livremente e confirmar que o conteúdo escala
corretamente (letterboxing do SCALED) sem distorcer proporção nem
quebrar o mapeamento de clique do mouse; fechar e reabrir o jogo depois
de restaurar a janela manualmente, confirmar que abre no tamanho
restaurado (não maximizado de novo).

### §34.42 — Leva pós-playtest: Fase H — Arena 1x1/3x3 + modal unificado
de fila (23/07/2026, maior escopo da leva)

Pedido do usuário: 2 modos novos de arena instanciada além do 2x2
existente — "Duelo" (1x1, soloqueue) e Arena 3x3 (grupo de 3) — com um
modal único de fila mostrando o placar de vitórias/derrotas por modo
(Fase E) e estrutura extensível pra um futuro 4º modo ("Campos de
Batalha", `next_implementations/battlefield_design.md` — fora de escopo
desta leva).

**`ARENA_MODES`** (`server/match_processor.py`, novo, generaliza o que
antes era hardcoded `_SPAWN_TEAM_A`/`_SPAWN_TEAM_B` fixos em 2): dict
`{mode_id: {team_size, label, spawns_a, spawns_b}}` pros 3 modos — todos
reusam o MESMO template (`ARENA_TEMPLATE`, renomeado de
`ARENA_TEMPLATE_2V2` — símbolo Python só usado dentro do próprio módulo,
seguro renomear; nenhum teste referencia o NOME da constante, só o valor
string `"maps/arena_poco_negro.csv"`) — confirmado por leitura direta do
CSV que as 2 salas de espera (linhas 2 e 32) têm piso aberto nas colunas
11-15, cabendo 1/2/3 spawns sem precisar de mapa novo.

**Fila por modo, não mais uma única lista**: `_arena_queue_2v2: list`
(WorldServer) virou `_arena_queues: dict[str, list]`, uma entrada por
`ARENA_MODES`. Cada fila guarda "tokens" — `party_id` nos modos de time
(2v2/3v3, igual antes) ou o PRÓPRIO `eid` do player no modo solo (1x1,
`team_size==1`) — resolvidos pro roster atual de eids só na hora do
pareamento (`_arena_members_for_token`, recarrega do estado VIVO, nunca
um snapshot congelado no join). `_tick_arena_queue` pareia cada fila
independentemente (sempre 2 tokens por vez — 2 "times" prontos, não
importa o tamanho de cada um).

**Exceção soloqueue (1x1)**: `request_arena_queue_join(eid, mode_id)`
pula toda a validação de `Party` quando `team_size==1` — o próprio eid
já É o "time de 1". Modos de time (2v2/3v3) continuam exigindo
líder+tamanho exato (mesma regra de antes, agora parametrizada por
`ARENA_MODES[mode_id]["team_size"]`). Um "já em fila" é checado
contra TODAS as filas de uma vez (`for q in self._arena_queues.values()`)
— um player/grupo não pode entrar em 2 modos ao mesmo tempo.

**`match_id` ganha prefixo do modo** (`f"arena{mode_id}_{...}"` →
`arena1v1_N`/`arena2v2_N`/`arena3v3_N`) — `match["mode_id"]` fica
guardado no dict da partida desde `_propose_match`, consultado em
`request_arena_accept` (spawn_list certo por modo) e `_finish_match`
(credita `arena_wins`/`arena_losses` — Fase E — no MODO REAL da
partida, não mais hardcoded `"2v2"`).

**Protocolo**: `ARENA_QUEUE_JOIN` ganha `{mode}` (C→S); `ARENA_QUEUE_STATE`/
`ARENA_MATCH_FOUND`/`ARENA_MATCH_START`/`ARENA_MATCH_RESULT` (S→C) todos
ganham `mode`/`{mode}` — cliente usa isso pra rotular corretamente qual
modo está em cada modal ("Fim de Partida — Arena 3x3", etc.), com
default `"2v2"` nos handlers pra compatibilidade se algum payload antigo
chegar sem o campo.

**Cliente — modal unificado** (`client/arena_handlers.py`):
`ARENA_MODE_LIST` (lista `[(mode_id, label), ...]`, não 3 botões
hardcoded — um 4º modo futuro é 1 entrada nova, o modal itera a lista
sem mudança de código) substitui o botão único "Fila de Arena 2x2"
por um botão persistente **sempre visível** ("Fila de Arena"/"Sair da
Fila" — antes só aparecia pro líder de um grupo de exatamente 2, mas
1x1 é soloqueue e precisa existir pra QUALQUER player) que abre o modal.
Cada linha do modal mostra o modo, o placar V/D (lido de
`CharStatsUI.get_data()` — Fase E, refrescado com um `CHAR_STATS_REQUEST`
ao abrir o modal) e um botão Entrar/Sair — `_arena_mode_eligible(mode_id)`
faz a MESMA validação do servidor (grupo certo/líder) só pra feedback
visual (desabilita o botão + mostra o motivo), nunca decide sozinho: o
servidor sempre revalida em `request_arena_queue_join`.

**Validado**: `tests/test_arena.py::TestArenaModesGeneralization` (9
testes novos) — modo inválido recusado; 1x1 soloqueue sem grupo; 2
solos pareiam com prefixo `arena1v1_` no match_id; aceite 1x1 libera
`can_engage`/dano de verdade; já em fila 1x1 não pode entrar em 2x2;
3x3 exige grupo de exatamente 3; 2 trios pareiam e spawnam 3 por lado;
vitória credita `arena_wins` no modo certo (não no 2v2 por engano);
filas de modos diferentes não interferem entre si. Os 50 testes PRÉ-Fase
H do arquivo (2v2 implícito via default `mode_id="2v2"`) continuam
passando SEM NENHUMA mudança — só 2 referências diretas a
`_arena_queue_2v2` viraram `_arena_queues["2v2"]`.
`tests/test_arena_client_ui.py` (17 testes novos, fixture leve sem
GameEngine completo — mesmo padrão de `_PvpCtxFixture` em
`test_client_ui.py`) — elegibilidade por modo (solo sempre elegível,
grupo errado/não-líder recusado), `ARENA_MODE_LIST` na ordem certa,
handlers de rede guardam o campo `mode` corretamente, clique
Entrar/Sair/Fechar no modal se comporta certo (manda o payload certo,
fecha o modal, ignora clique em botão inelegível). Suíte completa
459/459, rodada 3x.

**Não validado**: sessão manual — 2-6 jogadores reais testando os 3
modos simultaneamente (inclusive filas concorrentes de modos
diferentes), abrir o modal e conferir visualmente o placar/geometria
das linhas, confirmar que o botão persistente aparece pra QUALQUER
player (não só quem está em grupo) e que o texto de motivo (grupo
errado/não-líder) aparece legível na linha certa.

### §34.43 — Leva pós-playtest: Fase C — FLT de dano duplicado (retomada
e resolvida, 23/07/2026)

Pausada em sessão anterior por falta de reprodução detalhada — usuário
trouxe uma captura de tela (Escudo de Fogo, dano duplicado -16/-16 no
MESMO instante, HP só descontado 1x) e, durante a investigação, relatou
o MESMO sintoma em Calamidade Flamejante (skill sem nenhuma relação com
a primeira) — a combinação das duas provou que a causa era SISTÊMICA,
não de uma skill específica.

**Causa raiz** (`server/combat_processor.py::_process_player_attacks`):
dano de MOB→player é detectado por DIFERENÇA DE HP pós-tick
(`mob_delta = (hp_before - sfx_dmg - pvp_dmg) - hp_now` — "qualquer perda
de HP não explicada por DoT/PvP já rastreado é um golpe de mob não
visto"), gerando seu PRÓPRIO `combat_this_tick` fantasma quando
`mob_delta > 0`. Esse mecanismo existe desde o início do online (mob→
player nunca teve um ponto de emissão direto, só esse diff). Dano
PLAYER→PLAYER precisa se AUTO-EXCLUIR desse diff via
`_pvp_damage_this_tick` (dict per-tick) — senão o mob_delta "descobre" o
MESMO dano de novo e reporta como se fosse um golpe de mob não seguido,
duplicando o evento de rede (1 real do call site + 1 fantasma do
mob_delta, `attacker=-1` ou o último mob real que atacou aquele player).
HP só é subtraído 1x (`apply_damage_core` roda 1x) — só o EVENTO duplica,
por isso "FLT duplicado, dano recebido não" era exatamente o sintoma.

4 call sites JÁ faziam esse auto-registro manualmente (`combat_processor.
py::_process_pvp_attack`, `skill_processor.py`, `spell_completion_
processor.py` ×2) — um padrão repetido "fácil de esquecer num call site
novo", e foi exatamente isso que aconteceu 2 vezes:
- **Escudo de Fogo** (`engine/world_systems.py::deal_damage`, retaliation):
  `apply_damage_core(attacker_id, retaliation, killer_eid=target_id)` —
  nunca registrava.
- **Calamidade Flamejante** (`server/spell_completion_processor.py::
  _server_apply_magic_damage(..., report=True)`, usado por
  `_process_player_channeling`, tick de canalização): também nunca
  registrava.

**Fix — centralizado, não mais um call site a mais pra lembrar**:
`WorldServer._damage_tracker_composite` (`server/world_server.py`) — o
ÚNICO hook que roda pra QUALQUER dano com `killer_eid` válido (já usado
desde a Fase E pro placar de arena + `CharStatsTracker`) — agora TAMBÉM
registra em `_pvp_damage_this_tick[target_id]` sempre que **killer E
target são ambos players** (PvP de verdade). Os 4 call sites que faziam
isso à mão tiveram a linha removida (contariam 2x no dict senão). Guard
crítico: **só quando o killer também é player** — dano de MOB contra
player (auto-attack normal via `EnemyAISystem`, sem `combat_this_tick`
próprio) depende INTEIRAMENTE do `mob_delta` pra ser detectado;
registrar esse caso também zeraria o `combat_this_tick` de TODO ataque
de mob (regressão coberta por teste dedicado, ver abaixo).

**Diagnóstico ao vivo** (mesma metodologia já usada nesta leva): script
de duelo real (attacker sem escudo vs wearer com Escudo de Fogo,
`run_ticks` com captura de deltas) confirmou a dupla emissão ANTES do
fix (`{'attacker': 63, 'target': 62, 'damage': 11, 'source': 'skill'}` +
`{'attacker': -1, 'target': 62, 'damage': 11, 'source': 'auto'}`, mesmo
tick, mesmo valor, mesmo `hp_after`) e a resolução DEPOIS (1 evento só).
Mesma confirmação pra Calamidade Flamejante via `Channeling` simulado
contra um player em duelo.

**Validado**: `tests/test_flt_dedup.py` (3 testes novos) —
`TestEscudoDeFogoNaoDuplicaFLT` (agrupa por assinatura `(target, damage,
hp_after)` DENTRO de cada tick individual — não agregado, pra não
confundir 2 procs legítimos em ticks diferentes com uma duplicata real;
filtra ruído de mobs de fundo do `make_world_server()` corretamente),
`TestCalamidadeFlamejanteNaoDuplicaFLT` (mesma verificação pro tick de
canalização), `TestPvpDamageTrackingNaoQuebraMobDelta` (regressão
determinística — chama `_process_player_attacks` direto com um
`player_hp_snapshot` controlado, sem depender de IA/RNG de mob de
verdade — confirma que dano de MOB genuíno continua sendo detectado via
`mob_delta` normalmente). Também corrigido de passagem:
`tests/test_session.py::test_player_corpse_stays_dead_until_revive`
tinha só 5 ticks (0.25s) de margem pro mob acertar 1 golpe — flakiness
pré-existente exposta por esta leva ter adicionado testes novos ANTES
dele na ordem de execução (mesmo `random` global compartilhado entre
todo o processo pytest, classe de bug já documentada nesta sessão pra
Fase B) — bumped pra 240 ticks (12s), mesmo padrão já usado em
`TestPlayerAttacksMob`/`TestAutoAttackFlow`. Suíte completa 462/462,
rodada 3x.

**Não validado**: sessão manual real com 2+ clientes — confirmar que o
FLT de Escudo de Fogo e Calamidade Flamejante aparece só 1x cada agora
(o cenário exato que o usuário reportou com captura de tela).

### §34.44 — Menu de debug (F12): teleporte de mapa era client-only online
(23/07/2026)

Usuário validou Escudo de Fogo/Calamidade Flamejante (§34.43) e, testando
"Voltar ao Spawn" (A5, §34.36) via F12→aba Mapa→arena, viu tela preta e o
minimapa preso mostrando o mapa da arena mesmo depois de clicar "Voltar
ao Spawn". Investigação mostrou que **não era regressão do A5** — era
uma ferramenta de debug nunca adaptada pro modo online.

**Causa raiz**: `_debug_open_map`/bloco de `_debug_teleport_map` em
`game.py` chamava `self._do_transition(...)` DIRETO — essa função troca
`self._current_map_file`, recarrega o CSV local e limpa entidades
LOCALMENTE, sem nunca avisar o servidor (é o mesmo código usado desde a
era offline). Online, o servidor nunca soube da troca — `MapLocation.
map_file` do player continuava no mapa de origem. Ao clicar "Voltar ao
Spawn" logo depois, `_handle_unstuck` (A5) comparou o mapa AUTORITATIVO
(inalterado) contra o mapa principal, viu que já "estavam iguais" e só
fez um reposicionamento normal — nenhum `ZONE_CHANGE` foi necessário nem
mandado, então o `_current_map_file` do cliente (só mexido pelo F12
quebrado) nunca foi corrigido de volta. A5 em si nunca foi exercitado de
verdade por esse teste — confirmado rodando `tests/test_session.py::
TestUnstuck` (já existente, cobre exatamente esse cenário via
`transfer_player` real) e passando 2/2.

**Fix**: bloco de `_debug_teleport_map` (`game.py`) agora manda
`ZONE_CHANGE_REQ` (C→S) quando `self._net` existe (modo online) — o
MESMO caminho já usado por qualquer transição normal de mapa (entrar
numa caverna): servidor valida (`to_map` precisa estar em
`self.world_server._map_bundles`), executa `transfer_player` de
verdade, e só manda `ZONE_CHANGE` de volta — que aí SIM aciona
`_do_transition` no cliente (via `_handle_msg_zone_change`, já
existente). Offline continua chamando `_do_transition` direto (não há
servidor pra fazer o round-trip). Efeito colateral esperado e CORRETO:
mapas que só existem como instância por partida (ex.:
`arena_poco_negro.csv` fora de uma partida real) não estão em
`_map_bundles` standalone — o pedido é silenciosamente ignorado (nunca
deveria ser possível "noclipar" pra dentro do template da arena por
fora do sistema de fila).

**Validado**: `tests/test_session.py::TestZoneChangeReq` (2 testes
novos — nenhum teste cobria `_handle_zone_change_req` antes, apesar de
já ser usado hoje por transições reais de caverna) — mapa carregado
troca de verdade e manda `ZONE_CHANGE` com map_file/target_x/y
corretos; mapa não carregado (arena fora de partida) é ignorado sem
travar nem mandar nada. Suíte completa rodada 3x.

**Validado pelo usuário** (mesmo dia): caverna + "Voltar ao Spawn"
funcionando de verdade em sessão real. **Achado adicional**: teleporte
pra `arena_poco_negro.csv` continuava falhando — intenção real do
usuário era poder visitar a arena pra EDITAR o mapa (level design/QA),
não jogar uma partida de verdade. Causa: o template da arena nunca é
pré-carregado no boot (só via `_load_instance`, por partida) — a
validação original de `_handle_zone_change_req` (`to_map not in
_map_bundles`) recusava qualquer mapa nunca antes carregado, mesmo que
existisse no disco.

**Fix 2 (mesmo dia)**: `_handle_zone_change_req` (`server/session.py`)
agora carrega sob demanda (`WorldServer._load_map_for`, bundle
standalone, mesma função usada no boot pros mapas pré-carregados)
qualquer `to_map` que exista de verdade em `maps/*.csv` mas ainda não
tenha bundle — antes de tocar o disco, valida prefixo `"maps/"`, ausência
de `".."` e extensão `.csv` (rede de segurança contra um `to_map`
forjado por um cliente malicioso escapando da pasta `maps/`). Efeito:
QUALQUER mapa listado na aba Mapa do debug (incluindo templates só-
instância como a arena) agora é visitável sob demanda — o teleporte de
debug deixa de depender de o mapa já estar carregado por acaso.

**Validado**: `tests/test_session.py::TestZoneChangeReq` (+3 testes) —
template de arena carrega sob demanda e teleporta de verdade; caminho
fora de `maps/`/com `".."` é recusado sem tocar o disco; mapa inexistente
é ignorado sem travar. Suíte completa rodada 3x.

### §34.45 — Placeholder `{player_name}` em texto de quest (23/07/2026)

Pedido do usuário: poder usar o nome do personagem dentro do texto de
`description`/`completion` de uma quest (`content/quests_data.py::
QuestDef`), sem precisar de uma variável nova por texto.

**Uso**: escrever `{player_name}` em qualquer `description`/`completion`
de `QuestDef` — vira o nome do personagem automaticamente ao renderizar.
Ex.: `description="Bem-vindo, {player_name}! Prove seu valor."`.

**Implementação**: `engine/quest_logic.py::format_quest_text(text,
player_name)` — função pura (`.replace("{player_name}", player_name)`,
sem-op se `player_name` vazio, pra nunca sumir com a palavra
silenciosamente se `CharacterStats` não estiver carregado) — fonte
única, chamada nos 3 pontos que renderizam texto de quest em
`ui/quest_system.py`: diálogo de aceite (`_render_detail`), diálogo de
entrega (`_render_turnin`) e Diário de Quests (`QuestJournalSystem`).
Cada um busca `CharacterStats.name` do próprio `self.player_entity`
antes de formatar — sem estado novo, sem mudança de protocolo (o
servidor já manda `title`/`description`/`completion` como texto puro,
a substituição é 100% client-side no momento de desenhar).

**Validado**: `tests/test_quest_logic.py` (4 testes novos) —
substitui 1 ou várias ocorrências corretamente; texto sem o placeholder
fica intacto; nome vazio preserva o placeholder original (não
silenciosamente vira string vazia ilegível). Suíte completa 470/470,
rodada 3x.

### §34.46 — Recompensa de itens em quests: fixos + escolha (23/07/2026)

Pedido do usuário: quests podem conceder itens (não só XP/gold), com
suporte a "vários itens fixos" (com stack, ex.: 1 poção de vida + 2
poções de mana) E um pool de "escolha 1 entre N" (ex.: espada/maça/
machado) — os dois no MESMO diálogo de entrega já existente (sem modal
novo), ícones com tooltip no hover, seleção realçada + resto esmaecido
(igual skill em cooldown), "Concluir" só libera com a escolha feita.

**Schema** (`content/quests_data.py::QuestReward`): dois campos novos,
`items` (SEMPRE concedidos) e `choice` (escolhe 1). Cada entrada aceita
`"item_key"` (stack=1) ou `("item_key", stack)`. `item_key` é a CHAVE de
`content/item_table.py::ITEMS` (ex. `"training_sword"`, `"hp_potion"`,
`"mana_potion"` — não o nome de exibição), com fallback pra
`QUEST_ITEMS` (chave = nome de exibição, materiais de quest) via
`engine/quest_logic.py::resolve_reward_item_factory`. Exemplo:
```python
reward=QuestReward(
    xp=100, gold=20,
    items=("hp_potion", ("mana_potion", 2)),
    choice=("training_sword", "iron_mace", "apprentice_axe"),
),
```

**Protocolo**: `QUEST_TURN_IN` ganha `chosen_item` (C→S, só relevante se
`reward.choice` não-vazio). `INVENTORY_UPDATE` (S→C) — existia no
protocolo desde sempre mas NUNCA tinha sido implementado (nem
enviado nem havia handler client-side) — agora é usado pra empurrar
item(ns) concedido(s) fora do fluxo normal de loot.

**Servidor** (`server/session.py::_handle_quest_turn_in`): valida
`chosen_item` contra o pool ANTES de completar a quest (recusa a
entrega INTEIRA se o valor não bate com nenhuma entrada normalizada —
cliente adulterado/dessincronizado nunca ganha um item fora do
catálogo da quest). Servidor NÃO toca no Inventory ECS próprio pra
conceder — mesmo padrão já usado por loot (`server/loot_processor.py::
request_loot`): só resolve a fábrica, instancia o Item, serializa
(`server/server_death_handler.py::_serialize_item`, reaproveitado) e
manda via `INVENTORY_UPDATE` — o CLIENTE materializa na bag local, e o
`INV_SYNC` periódico do cliente mantém o mirror do servidor atualizado
depois. `item_key` que não resolve em nenhum catálogo (typo do autor de
conteúdo) é só ignorado com um warning no log — não derruba a entrega
inteira nem os outros itens válidos.

**Cliente**: lógica de reconstrução+stack de item (antes só inline em
`_handle_msg_loot_result`) foi extraída pra
`NetworkHandlers._grant_items_to_inventory()` — fonte única, reusada
por `LOOT_RESULT` (loot de corpse) e o novo `_handle_msg_inventory_update`
(recompensa de quest).

**UI** (`ui/quest_system.py::QuestDialogSystem._render_turnin`): faixa
FIXA (não rolável) de ícones logo acima do botão Concluir — itens fixos
primeiro (só tooltip, sem clique), depois "Escolha uma recompensa:" +
ícones do pool (clicáveis, 1 selecionado por vez, mesmo padrão visual de
skill ativa/inativa da hotbar). Tooltip reaproveita
`ui/ui_helpers.py::item_tooltip_lines` + `ui/icon_manager.py::ICONS` —
mesmo mecanismo de Inventário/Loja/Forja. `QuestDialogSystem` ganhou
`pending_tooltip` (sem underscore) — mesmo padrão de bridging de
LootSystem/ShopSystem/CraftingSystem (`game.py` copia pra
`self._pending_tooltip` no fim do frame, só faltava esse 1 caso).
Concluir fica esmaecido/sem-ação (não fecha o diálogo, não manda
QUEST_TURN_IN) enquanto `reward.choice` não-vazio e nada foi
selecionado ainda.

**Bug real pego pelo próprio teste**: a primeira versão só registrava o
rect de hit-test do ícone de escolha DENTRO do bloco `if hovered` — só
"funcionava" em jogo de verdade por coincidência (render e clique leem
`pygame.mouse.get_pos()` no mesmo frame), mas falhava em qualquer cenário
onde os dois não coincidissem exatamente. `tests/test_quest_reward_ui.py`
pegou isso na primeira rodada (rects vazios) antes de qualquer sessão
manual — rect agora é sempre registrado, tooltip que fica condicional
ao hover.

**Validado**: `tests/test_quest_logic.py` (+5 testes — normalize/resolve),
`tests/test_quest_turn_in.py` (8 testes novos — item fixo único, múltiplos
com stack, escolha válida, fixos+escolha juntos, escolha inválida recusa
tudo, escolha ausente quando obrigatória recusa, item_key com typo é
ignorado sem derrubar o resto, sem itens não manda INVENTORY_UPDATE),
`tests/test_client_ui.py` (+3 testes — `_grant_items_to_inventory`/
`_handle_msg_inventory_update`), `tests/test_quest_reward_ui.py` (6
testes novos — rects populados, clique seleciona, Concluir sem seleção
não manda nada, Concluir com seleção manda `chosen_item` certo, sem pool
de escolha manda direto, trocar de item re-seleciona). Suíte completa
492/492, rodada 3x.

**Validado pelo usuário** (24/07/2026, print anexado): quest de teste
funcionou de ponta a ponta (ícone fixo + escolha + item chegando na bag),
com 1 ponto de melhoria visual reportado — ver Fix 2 abaixo.

**Fix 2 — ícone de recompensa colado no botão Concluir com vão vazio
acima (24/07/2026)**: `_render_turnin` calculava `reward_top` fixo, a
partir do botão pra cima (`btn_y - self._u(10) - reward_area_h`),
ignorando onde o texto rolável (título/descrição/texto de conclusão)
realmente terminava. Pra quest com texto curto, isso deixava um vão vazio
grande entre o fim do texto e a faixa de ícones — o usuário anexou print
com retângulos indicando a posição real (baixa, colada no botão) vs. a
desejada (mais alta, logo após o texto). Fix: calcular também
`content_end_y` (fim real do conteúdo rolável) e usar
`reward_top = max(view_top, min(content_end_y, max_reward_top))` — a
faixa agora acompanha texto curto (fica logo abaixo) mas continua presa
perto do botão pra texto longo que precisa de scroll (sem sobrepor
conteúdo). Regressão coberta por
`test_icones_ficam_logo_apos_texto_curto_nao_colados_no_botao`
(`tests/test_quest_reward_ui.py`) — confirmado via `git stash` que o
teste falha sem o fix (`rect.y=320` vs. exigido `<272`) e passa com ele.
Suíte completa 493/493, rodada 3x.

### §34.47 — Quest dada por 1 NPC, entregue em OUTRO (24/07/2026)

Pedido do usuário: quest "bem_vindo_guerreiro" configurada com
`quest_ids=("bem_vindo_guerreiro",)` em Caterina Alisarf (só dá) e
`turn_in_ids=("bem_vindo_guerreiro",...)` em Avido Faseo (só recebe) —
ao completar os objetivos, o indicador de "concluída" (ícone `?`/marker
de mapa) aparecia sobre os DOIS NPCs, quando deveria só aparecer em
Avido.

**Causa raiz** (`engine/components.py::QuestGiver`): `turn_in_ids` vazio
tem fallback documentado "= quest_ids" — pensado pra quests simples onde
o mesmo NPC dá e recebe. Caterina tem `turn_in_ids=()`, então
`QuestDialogSystem._get_completable_quests`/`_get_inprogress_quests`
(`ui/quest_system.py`) caíam nesse fallback e usavam
`giver.quest_ids = ("bem_vindo_guerreiro",)` como se ela TAMBÉM aceitasse
a entrega — mesmo Avido já sendo configurado como o entregador de
verdade. Como `marker_for()` e o menu "Quests" do diálogo dependem
inteiramente dessas duas funções, e o servidor (`server/session.py::
_handle_quest_turn_in`) nunca valida QUAL NPC está entregando (só
`quest_id`/objetivos/`chosen_item`), o bug não era só visual — a entrega
também teria sido aceita se o jogador clicasse "Concluir" no diálogo da
Caterina.

**Fix**: `QuestDialogSystem._turn_in_ids_for(giver)` (novo) — só usa o
fallback `quest_ids` pra qids que NENHUM outro `QuestGiver` do mundo já
reivindica explicitamente em `turn_in_ids` (`_quests_claimed_for_turn_in()`,
varre todos os `QuestGiver`). `_get_completable_quests`/
`_get_inprogress_quests` passaram a chamar esse helper em vez do fallback
inline. Quests simples de 1 NPC só (ninguém mais reivindica) continuam
funcionando exatamente igual — só quando existe um NPC de entrega
explícito em outro lugar é que o NPC "só dá" para de aparecer como
aceitando.

**Validado**: `tests/test_quest_split_npc.py` (4 testes novos — em
progresso só aparece no NPC de entrega, completável só aparece no NPC de
entrega, marker do NPC que só dá vira `None` após aceitar, fallback de
NPC único sem split continua funcionando). Confirmado via `git stash` que
3 dos 4 testes falham sem o fix. Suíte completa rodada 3x.

### §34.48 — Janela abre "tela cheia"/borrada + fontes esticadas/modal
cortado em janela maximizada (24/07/2026, pesquisa aprofundada)

Pedido do usuário: tela de login abre parecendo tela cheia (maior que o
pedido, borrada); dentro do jogo, ao maximizar (Fase G, §34.44), fontes
ficam esticadas/cortadas e alguns modais têm shape cortado — quer
pixel-perfect em qualquer resolução, com o resize já tratado
automaticamente (mesmo princípio do zoom in-game, que já é pixel-perfect
hoje).

**Pesquisa** (pygame-ce issues/docs + Microsoft docs, ver fontes abaixo):
processo Windows sem manifesto de DPI awareness roda "DPI-unaware" — o
SO aplica bitmap-stretch de COMPATIBILIDADE em cima da janela inteira
pra compensar (ex.: monitor a 150% de escala → janela de 1280×720
pedida é fisicamente desenhada/esticada como se fosse ~1920×1080),
produzindo exatamente "parece tela cheia" + borrado — e isso já
acontece na tela de login (`main.py`), ANTES de qualquer `SCALED`/lógica
do jogo entrar em cena, porque é 100% um comportamento do Windows, não
do pygame. Separadamente, `pygame.SCALED` (usado desde §34.44/vsync-fix
de 14-19/07) faz scaling PIXEL-PERFEITO (múltiplo inteiro) em janela
redimensionável normal, mas muda pra "stretch pra caber na menor
dimensão" quando maximizado/fullscreen — comportamento não-configurável
do próprio pygame-ce (issue #2611, sem flag pra forçar integer-only
nesse caso). Confirmado também: `vsync=1` **não** exige mais `SCALED`/
`OPENGL` desde pygame-ce 2.2.0 (mudou depois do vsync-fix documentado em
14-19/07) — mas o profiling ORIGINAL deste projeto (`logs/
client_prof.log`, mesma investigação) mediu o bug real (flip
inconsistente 5-47ms) especificamente SEM `SCALED` nesta versão (2.5.7)
— não dá pra confirmar sem reteste que tirar `SCALED` não reintroduziria
aquilo, então o fix desta rodada NÃO mexe nesse flag (só na causa
isolada e comprovada: DPI awareness do processo).

**Fix (fase 1, aplicado)**: `main.py::_make_dpi_aware()` — chama
`SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` (Win10 1703+) ANTES
de `pygame.init()`/qualquer `set_mode`, com cascata de fallback (Per-
Monitor → System DPI Aware) pra Windows mais antigo, cada nível só
tentado se o anterior não existir/falhar — nunca derruba o boot. Isso
impede o Windows de aplicar o bitmap-stretch de compatibilidade em
QUALQUER janela do processo (login E jogo), o que também remove uma
camada de distorção que se somava em cima do próprio scaling do
`SCALED` durante maximização — deve reduzir bastante (talvez resolver
por completo) o "esticado/cortado" relatado, já que o `SCALED` passa a
calcular sua escala a partir do tamanho REAL da janela em vez de um
tamanho já inflado pelo Windows.

**Reteste do usuário (fase 1)**: DPI a 100% na máquina dele (confirmado
via `GetDeviceCaps`/`Screen.PrimaryScreen`, resolução real 1920×1080,
área útil 1920×1040 com barra de tarefas) — ou seja, a fase 1 não tinha
como ajudar NESSA máquina especificamente (não havia bitmap-stretch de
DPI pra remover), o que bate com o usuário reportar "não mudou muita
coisa". Print novo mostrou o sintoma mais preciso: a MESMA string
("Gorrtiel: teste") legível numa linha do chat e corrompida
("Gorrticl: tcstc") na linha seguinte — corrupção de traço fino
DEPENDENTE DE POSIÇÃO, não borrão uniforme.

**Fase 2 — causa raiz real**: `pygame.SCALED` calcula 1 fator de escala
UNIFORME (preserva aspect ratio, sem esticar X/Y diferente) = `min(
janela_w/lógico_w, janela_h/lógico_h)`. Como a resolução lógica ficava
fixa em `1280×720×scale` enquanto a janela maximizada virava o tamanho
real do monitor, esse fator quase nunca é um número INTEIRO (nesta
máquina: `1040/720 = 1.444...`). SDL usa amostragem nearest-neighbor por
padrão — escala fracionária com nearest-neighbor duplica/descarta linhas
de pixel de forma inconsistente conforme a posição (fenômeno conhecido
de upscale de pixel art em razões não-inteiras), exatamente o padrão
"legível aqui, corrompido ali" do print (traço fino do meio do 'e'
sobrevive numa linha, some na de baixo, virando 'c'). Tentativa de
verificação visual direta (screenshot real de tela via probe pygame)
bloqueada pelo sandbox do ambiente (processo em background não conseguiu
criar janela na desktop interativa) — diagnóstico fechado por
documentação SDL (scaling de logical size é sempre uniforme/aspect-
preserving) + evidência fotográfica do próprio usuário, não por
reprodução visual própria.

**Fix (fase 2, tentativa inicial — REVERTIDA, ver §34.49)**:
`game.py::_sync_logical_size_to_window()` reagia ao evento nativo
`WINDOWMAXIMIZED` recriando o display (`_apply_logical_size()`) com a
resolução lógica = tamanho real da janela maximizada. **Causou um crash
real** (segfault) reportado pelo usuário — ver §34.49 pra causa raiz
completa (não era o evento em si, era `set_mode()` sendo chamado 2x sem
o ciclo `pygame.display.quit()+init()` antes) e o fix definitivo.

**Fontes**: [pygame-ce #931 — fullscreen scaling incorreta em Windows
high-DPI](https://github.com/pygame-community/pygame-ce/issues/931),
[pygame-ce #2611 — SCALED não oferece flag de integer-only ao
maximizar](https://github.com/pygame/pygame/issues/2611),
[Microsoft — SetProcessDpiAwarenessContext](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setprocessdpiawarenesscontext),
[pyga.me — display docs (vsync não exige mais SCALED/OPENGL desde
2.2.0)](https://pyga.me/docs/ref/display.html).

### §34.49 — Crash real (segfault) ao trocar resolução + menu explícito
de modo de janela (24/07/2026)

**O que aconteceu**: usuário testou o fix da §34.48 fase 2. Clicou no
botão nativo de maximizar da janela (entre minimizar e fechar) — o jogo
foi pra tela cheia de verdade, sem decoração, e não havia como reverter
pela UI. Fechou e reabriu o jogo — continuou em tela cheia (config
salvo). Abriu o menu de resolução e trocou pra 1280×720 — o processo
morreu com `Fatal Python error: pygame_parachute: (pygame parachute)
Segmentation Fault`, stack apontando pra dentro de
`_apply_logical_size` → `pygame.display.set_mode()`.

**Causa raiz (confirmada por reprodução isolada, não só pelo crash.log)**:
chamar `pygame.display.set_mode()` uma SEGUNDA vez em cima de um display
já inicializado **segfaulta nesta stack** (pygame-ce 2.5.7 + SDL 2.32.10
+ driver/GPU desta máquina) — reproduzido com um script isolado que
recria o display em sequência (windowed → maior → windowed de novo):
crash consistente na 2ª chamada de `set_mode()`, mesmo SEM nunca ter
passado por fullscreen ou pela API nativa de maximizar. Ou seja: **não
era especificamente sobre reagir ao evento `WINDOWMAXIMIZED`** (a
hipótese original da fase 2) — era `set_mode()` sendo chamado 2x sem o
ciclo `pygame.display.quit()` + `pygame.display.init()` antes. Esse
ciclo já era usado em `main.py` (linhas ~151-152, ao trocar da tela de
seleção de personagem pra um `GameEngine` novo) — só nunca tinha sido
aplicado dentro do próprio `GameEngine` (slider de escala/`_apply_scale`,
que já existia ANTES desta sessão, sempre teve esse mesmo risco latente,
só não tinha sido acionado na sequência exata que crasha).
**Confirmado o fix**: adicionar `pygame.display.quit()`+`init()` antes de
CADA `set_mode()` de recriação eliminou o crash — testado em sequência
completa (windowed → windowed_fullsize → fullscreen → windowed_fullsize
→ windowed) 2x seguidas, sem falha nenhuma.

**Fix definitivo**: `game.py::_recreate_display(win_w, win_h, flags)`
(novo, único ponto que chama `set_mode()` fora do boot) — SEMPRE
`pygame.display.quit()` + `pygame.display.init()` antes de `set_mode()`,
e reaplica `pygame.display.set_caption()` (esse ciclo reseta o título da
janela pro padrão do SDL). `_apply_logical_size()` e o branch
`"fullscreen"` de `_compute_and_set_window_mode()` passaram a usar esse
helper. `main.py` ganhou o mesmo ciclo no fluxo de "Deslogar" (linha
~176, `set_mode()` direto depois que `GameEngine.run()` pode ter deixado
o display em QUALQUER modo, inclusive `FULLSCREEN`) — era o mesmo risco
latente, só ainda não reportado.

**Reação ao evento nativo `WINDOWMAXIMIZED`/`WINDOWRESTORED` REVERTIDA
por completo** — nenhum dos dois mais chama `set_mode()`/recria o
display; só registram `_window_mode_pref` (persistido, migrado no
próximo boot). `set_mode()` NUNCA deve ser chamado em reação a um evento
nativo de janela — só no boot (antes do loop rodar) ou em reação a um
clique explícito de menu (mesmo padrão que `resolution:<scale>` sempre
usou, agora generalizado).

**Feature nova (pedido do usuário, na mesma mensagem do crash — depois
simplificada, ver addendum abaixo)**: menu "Janela" (Configurações >
Janela, ao lado de "Resolution") com 3 modos EXPLÍCITOS, cada um só
acionado por clique — nunca automático:
- **"Janela"** — janela normal, resolução do slider de escala (Resolution:
  1280×720/1600×900/1920×1080), 3 botões nativos livres (clicar
  maximizar não crasha mais, só volta a ter a distorção de fonte da
  §34.48 fase 1 até o jogador trocar de modo pelo menu).
- **"Janela (tamanho da tela)"** — detecta a resolução do monitor
  (`pygame.display.get_desktop_sizes()`), redimensiona a janela pra caber
  quase toda a tela (folga de 16px de largura / 80px de altura — barra
  de tarefas e barra de título sempre visíveis, NUNCA usa a API nativa
  de maximizar), resolução lógica = tamanho real da janela → fator do
  SCALED sempre 1.0, zero distorção.
- **"Tela cheia"** — fullscreen de verdade (`pygame.FULLSCREEN`),
  resolução lógica = resolução nativa do monitor → mesmo motivo, fator
  sempre 1.0.
`window_mode` salvo com valor antigo `"maximized"` migra automaticamente
pra `"windowed_fullsize"` no boot (API nativa de maximizar removida do
boot por completo).

**Validado**: suíte completa 497/497, rodada 3x (não cobre `GameEngine`/
transições de janela — nenhum teste já instanciava a classe inteira,
mesma limitação anterior). Sequência de recriação de display (windowed →
windowed_fullsize → fullscreen → windowed_fullsize → windowed) testada
isoladamente 2x com o ciclo quit()+init() — sem crash.

**Addendum (24/07/2026, mesmo dia — simplificação pedida pelo usuário)**:
testado no jogo real, sem crash — mas com a `Resolution` em 1920×1080
(batendo com o monitor), o modo "Janela" (tamanho FIXO) ficava
visualmente IDÊNTICO a "Tela cheia" (sem nenhuma borda visível),
tornando os 3 modos redundantes/confusos. Pedido do usuário: reduzir pra
só 2 modos (eliminar o "Janela" de tamanho fixo — ambos os que sobram já
usam resolução lógica = tamanho real, então o slider de escala não fazia
mais diferença nenhuma pra eles mesmo), apresentados como 1 TOGGLE de 2
segmentos ("Modo:  Tela cheia | Janela") dentro do PRÓPRIO submenu
"Resolution" — sem botão novo no menu principal — e "Janela" (=
`windowed_fullsize`) como modo padrão.
- `client/menu_handlers.py`: `_draw_window_mode_submenu`/
  `_WINDOW_MODE_OPTIONS` removidos; `_BTNS` do menu principal perdeu a
  entrada "Janela"; `_draw_resolution_submenu` perdeu o picker de escala
  antigo (1x/1.25x/1.5x, `SCALE_OPTIONS`) e ganhou o toggle de 2
  segmentos (`_WINDOW_MODES = [("Tela cheia","fullscreen"), ("Janela",
  "windowed_fullsize")]`) no lugar — mesmo mecanismo de clique
  (`window_mode:<mode>`), já existente em `game.py`.
- `game.py`: nenhuma mudança de lógica — `_apply_window_mode`/
  `_compute_and_set_window_mode` continuam suportando os 3 valores
  internamente (`"windowed"` vira só um fallback de erro interno, não
  mais alcançável pela UI); default de boot já era `"windowed_fullsize"`
  desde a versão anterior, sem mudança.
- `ui/settings_screen.py` (tela de resolução PRÉ-login, `SCALE_OPTIONS`)
  **não foi tocada** — é uma tela separada, roda antes do `GameEngine`
  existir (sem auto-detecção de tela ainda disponível ali); fora de
  escopo deste pedido.

**Validado**: usuário confirmou que o crash não voltou depois do fix
principal. Suíte completa rodada 3x (497/497) depois da simplificação do
menu. **Não validado**: reteste visual do toggle simplificado no jogo
real (era só sobre o menu ter 3 modos confusos — a lógica de
`window_mode:<mode>` em si já tinha sido exercitada e não mudou).

**Addendum 2 (24/07/2026, mesmo dia — 2 correções do usuário)**: (1) o
"toggle de 2 segmentos" acima (2 retângulos lado a lado) foi rejeitado —
usuário pediu um toggle DE VERDADE (1 controle só, não "2 botões"); (2)
o picker de escala (Resolution, 1x/1.25x/1.5x) tinha sido removido do
submenu por engano — usuário só pediu pra ADICIONAR o toggle de modo de
janela ali, não substituir o picker que já existia.
- `client/menu_handlers.py::_draw_resolution_submenu`: picker de escala
  restaurado (idêntico ao original, `SCALE_OPTIONS`, ação
  `resolution:<val>`). Abaixo dele, nova linha "Modo:      Tela cheia/
  Janela" com um toggle de verdade — 1 track (pílula arredondada) + 1
  knob (círculo) que desliza pra esquerda (Tela cheia) ou direita
  (Janela) conforme `_window_mode_pref`; clicar em qualquer parte da
  faixa alterna pro modo OPOSTO (não precisa acertar um lado
  específico) — mesma ação `window_mode:<mode>` de antes.
- `ui/ui_sizes.py::MENU_RESOLUTION_H`: 220 → 270 (espaço extra pra a
  linha do toggle, sem apertar o botão Voltar).
- `game.py`: nenhuma mudança — mesma ação `window_mode:<mode>` de antes.

**Validado**: suíte completa rodada 3x (497/497). Reteste visual do
toggle (faixa+bolinha) e do picker de escala restaurado confirmado pelo
usuário em jogo real (24/07/2026) — inclusive o modo "Tela cheia", não
testado explicitamente até então.

### §34.50 — Modal de loot (e outros) some depois de trocar modo de
janela (24/07/2026)

Pedido do usuário: "aconteceu algo com o modal de loot, não aparece na
tela" — reportado logo depois de testar o toggle Tela cheia/Janela da
§34.49.

**Causa raiz**: `GameEngine._rebuild_screen_refs()` (chamada sempre que
`self.screen` é substituído por um objeto `Surface` NOVO — todo resize
via `_apply_logical_size`, incluindo o toggle de modo de janela) só
setava `sys.screen = new_screen`. Mas sistemas que herdam de
`engine.world_systems.System` — `LootSystem`, `ShopSystem`,
`QuestDialogSystem`, `QuestJournalSystem`, `TrainerSystem`,
`BlacksmithSystem`/`CraftingSystem` — **nunca têm um atributo
`.screen`**, só `world_surf`/`hud_surf` (ver docstring de `System`).
`hasattr(sys, "screen")` era `False` pra todos eles — `sys.screen =
new_screen` era um no-op silencioso. `world_surf` escapa por acaso
(`GameEngine._assign_world_surf()` reatribui todo frame, independente
disso); `hud_surf` não tem esse refresh em NENHUM outro lugar — só
dentro de `_rebuild_screen_refs`. Resultado: depois de QUALQUER resize
ao vivo, `hud_surf` desses sistemas fica apontando pra a `Surface`
ANTIGA (órfã, nunca mais desenhada na tela real) pelo resto da sessão —
o modal continua sendo "desenhado", só que numa superfície que ninguém
mais mostra.

Bug **pré-existente** (a mesma falha já existia no antigo picker de
escala/`_apply_scale`, usado desde antes desta leva) — ficou dormant
porque trocar de escala ao vivo durante uma sessão real era raro. Virou
visível na hora porque o modo de janela (§34.48/49) tornou resize ao
vivo uma ação comum (o próprio toggle que acabou de ser entregue).

**Fix**: `_rebuild_screen_refs()` reescrita com um helper `_refresh(obj)`
que atualiza `.hud_surf` **e** `.screen` em qualquer sistema que tenha o
atributo (a maioria dos afetados só tem `hud_surf`; `TalentSystem`/
`CharStatsUI`/`SkillLevelUI`/`MapOverlay`/`Minimap` — não são `System`
subclasses — só têm `.screen`, continuam funcionando igual).

**Validado**: `tests/test_client_ui.py` — 2 testes novos com stubs que
imitam os dois contratos (`_StubHudSurfSystem` só `hud_surf`,
`_StubScreenOnlySystem` só `.screen`), chamando o método real
(`GameEngine._rebuild_screen_refs`) via injeção de método num fixture
leve (mesmo padrão de `_client_pvp_context`/`_local_eid_to_server_eid`
já usado no arquivo). Confirmado via `git stash` que o teste de
`hud_surf` falha sem o fix (`Surface(10x10)` continua no lugar de
`Surface(20x20)`) e passa com ele. Suíte completa 499/499, rodada 3x.
Usuário confirmou em jogo real (24/07/2026) que Loja, diálogo de quest,
Treinador e Forja — mesma causa raiz, mesmo fix — também voltaram a
aparecer normalmente depois de trocar de modo de janela.

### §34.51 — Quests novas (dano/skill) + itens interativos no mapa
(25/07/2026, leva planejada — ver plano completo salvo em
`C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`)

Pedido do usuário em 3 partes: objetivo de quest "causar dano por
auto-attack num alvo específico", skill como recompensa de quest, e um
sistema novo de itens interativos no mapa (planta/pergaminho/ferramenta
saqueável, com trava de quest e possibilidade de conceder quest nova ao
ser coletado). No meio da conversa, investigação confirmou um bug real
já existente: loot condicional de quest (`roll_conditional_loot`) é
decidido 1x na morte do mob (QuestLog do first-attacker) e fica FIXO no
corpo — qualquer membro do MESMO GRUPO pode saquear depois e ver/pegar o
item mesmo sem a quest. Plano de 7 fases (Q1→Q2→L1→M1→M2→M3→M4),
executadas uma por vez com commit/versão/build próprios.

**Fase Q1 — Objetivo "auto_attack_hit"**: conta ACERTOS de auto-attack
(não dano acumulado, decisão do usuário — dano numérico fica pra depois
se precisar) contra um alvo (nome/raça/`"*"`), sem contar hit de skill.

- `content/quests_data.py`: novo tipo documentado no topo do arquivo.
- `engine/quest_logic.py::match_objective`: novo bloco, cópia do padrão
  de `"kill"` (target em nome/raça/`"*"`).
- `ui/quest_system.py::_obj_label`: label "Acertar {alvo} com ataque
  básico".
- `server/combat_processor.py::_process_player_attacks`: logo após
  `_combat_this_tick.append(...)` (mesmo ponto que já resolve `_outcome`
  pro contador de Punho no Queixo), dispara `quest_events.fire(
  "auto_attack_hit", player_eid=..., name=..., race=...)` só quando
  `_pnq_hit` (mesmo critério já em produção: `_outcome not in ("miss",
  "dodge", "parry", "block")`) — reaproveita o filtro existente em vez de
  inventar um novo. Só PvE (`_process_player_attacks`), igual "kill" —
  `_process_pvp_attack` não ganhou o mesmo gatilho (fora de escopo,
  mesma convenção do "kill" que também nunca dispara em PvP).

**Validado**: `tests/test_server.py::TestAutoAttackHitQuestEvent` (3
testes novos — acerto soma 1, 3 acertos completam objetivo count=3, miss
forçado via `cs.acerto=0.0` NÃO soma nada). Confirmado via `git stash`
que 2 dos 3 testes falham sem o fix em `combat_processor.py`. Suíte
completa rodada 3x.

**Fase Q2 — Skill como recompensa de quest**: `QuestReward` ganha
`skill: str = ""` (chave de `SKILL_CATALOG`, não nome de exibição — 1 só
por quest, sem escolha entre skills por enquanto).

- **Diferença de design vs. recompensa de item** (documentada no código):
  aprender skill já tem um portão de autorização server-side
  (`is_skill_authorized()`, `engine/world_systems.py`) que olha o
  `PlayerSkills.learned_skill_ids` DO PRÓPRIO SERVIDOR — então, ao
  contrário de item (servidor nunca toca o Inventory, só manda
  `INVENTORY_UPDATE` e confia no `INV_SYNC` do cliente depois), a
  recompensa de skill precisa que o SERVIDOR grave direto em
  `ps.learned_skill_ids` (+ insira o objeto `Skill` via
  `PlayerSkills._make_skill`, mesma função que
  `ui/trainer_system.py::_do_learn` usa) NO MOMENTO da entrega — só
  DEPOIS avisa o cliente pra ele materializar o mesmo localmente.
- **Protocolo**: `SKILL_GRANTED` novo (S→C, `shared/messages.py`) —
  `{"skill_id": str, "name": str}`.
- **Servidor** (`server/session.py::_handle_quest_turn_in`): se
  `qdef.reward.skill`, resolve em `SKILL_CATALOG`; classe do skill
  diferente da classe do player → ignora com warning (mesmo espírito de
  `item_key` inválido, não derruba o resto da entrega); já aprendida →
  não duplica (nem manda `SKILL_GRANTED` de novo); senão grava em
  `learned_skill_ids` + insere `Skill` num slot livre + manda
  `SKILL_GRANTED`.
- **Cliente** (`client/network_handlers.py::_handle_msg_skill_granted`):
  mesma lógica de materialização de `_do_learn` (sem custo/nível, já
  concedido pelo servidor).
- **UI** (`ui/quest_system.py::QuestDialogSystem`): ícone da skill entra
  na MESMA faixa de itens fixos do diálogo de entrega (não clicável, só
  tooltip) — `_reward_skill_info()` resolve pra exibição,
  `_reward_skill_tooltip_lines()` monta a prévia (nome/desc/cooldown/
  custo de raiva) lendo `SKILL_CATALOG` DIRETO (diferente de
  `client/tooltip_handlers.py::_skill_tooltip_lines()`, que exige um
  objeto `Skill` "ao vivo" com estado de cooldown/proc — não serve pra
  uma skill que o player ainda não tem), `_draw_reward_skill_icon()`
  desenha o ícone (`skill_<id>`, mesma convenção da hotbar) e liga o
  tooltip no hover — pedido explícito do usuário (25/07/2026).

**Validado**: `tests/test_quest_turn_in.py::TestQuestTurnInRewardSkill`
(4 testes — skill válida da classe certa concedida + `SKILL_GRANTED`
mandado, classe errada ignorada sem derrubar o resto, chave inexistente
no catálogo ignorada, já aprendida não duplica slot nem reenvia),
`tests/test_client_ui.py` (+2 testes — `_handle_msg_skill_granted`
adiciona a `learned_skill_ids`/hotbar, já aprendida não duplica slot),
`tests/test_quest_reward_ui.py` (+4 testes — `_reward_skill_info`
resolve/ignora chave inválida, `_render_turnin` com skill não quebra,
tooltip aparece no hover e NÃO aparece fora do hover). Confirmado via
`git stash` que 10 dos 12 testes novos falham sem a implementação. Suíte
completa rodada 3x.

**Achado de teste (não é bug de produto — cuidado pra testes futuros)**:
`data/game.db` é um arquivo SQLite REAL e persistente entre execuções de
teste (`server/auth.py::DB_PATH`), não efêmero — `test_quest_turn_in.py`
rodado isolado passava 12/12, mas na suíte completa 2 dos testes de
skill falhavam, porque o username reusado ("qtsusera"/"qtsuserd") já
tinha a skill aprendida/persistida de uma corrida anterior (a lógica de
"já aprendida, não duplica" que acabou de ser implementada tornou o
teste sensível a isso — recompensa de ITEM nunca teve esse problema
porque sempre re-concede, sem checar "já tem"). Fix: `_ready_session`
(no teste) agora limpa explicitamente `learned_skill_ids`/slots das
skills usadas no teste logo após o login, garantindo baseline
determinístico independente do histórico do banco — qualquer teste
futuro que dependa de "personagem ainda não tem X" precisa do mesmo
cuidado (usernames fixos + banco real = estado pode vazar entre
corridas).

**Fase L1 — Loot condicional de quest passa a ser resolvido por jogador**
(retrofit de corpo de mob, base que a Fase M3 dos itens de mapa também
usa).

**Causa raiz confirmada** (usuário perguntou se isso tinha problema em
grupo — investigação achou que sim): `roll_conditional_loot` (ex.: Pelo
de Urso pra objetivo `collect_item`) rodava UMA VEZ na morte do mob,
contra a `QuestLog` só do first-attacker (`server/server_death_handler.py`),
e o resultado ficava GRAVADO FIXO dentro do corpse. Como
`server/loot_processor.py::request_loot` já permite qualquer membro do
MESMO GRUPO do dono saquear o mesmo corpse ("free-for-all dentro do
grupo", decisão de 17/07/2026), um colega SEM a quest que saqueasse
depois via/pegava o item que só deveria existir pra quem tinha a quest.

**Fix**: o loot condicional passa a ser resolvido POR JOGADOR, na hora
que CADA jogador interage com o corpse (nunca mais 1x na morte contra o
first-attacker) — cacheado, nunca re-sorteado pro mesmo jogador (mesma
decisão já confirmada com o usuário: reabrir o modal não dá nova
chance).
- `server/server_death_handler.py`: bloco de rolagem condicional na morte
  REMOVIDO; `pending_loot.append(...)` ganha `mob_name`/`mob_race` (pra
  a resolução por jogador saber contra qual mob checar depois).
- `server/loot_processor.py`: `_process_loot_drops` grava `mob_name`/
  `mob_race`/`quest_rolls: {}` no dict do corpse. `_resolve_conditional_loot_for(
  corpse, player_eid)` (novo, método do mixin) resolve 1x — cacheado em
  `corpse["quest_rolls"][player_eid]` — e nunca resolve de novo pro mesmo
  jogador (mesmo se ele nunca chegou a retirar o item: fica esperando lá,
  mas o SORTEIO em si não repete). `request_loot` usa essa função como
  fallback (cobre quem entrou no grupo depois de já existir a
  notificação) e mescla o resultado nos itens devolvidos (`take="all"`
  soma comum+pessoal; `take="item"` procura no pote comum primeiro,
  depois no pessoal).
- `server/session.py`: o loop que já mandava `LOOT_AVAILABLE`
  individualmente pra cada membro do grupo (`_loot_recipients`, já existia
  desde 17/07 — NÃO era um broadcast único) agora chama
  `_resolve_conditional_loot_for` PRA CADA destinatário e soma o resultado
  só na mensagem DAQUELE destinatário — cada jogador vê exatamente o que
  é seu, mesmo abrindo o MESMO corpse ao mesmo tempo que o colega.

**Validado**: `tests/test_session.py::TestConditionalLootPerPlayer` (5
testes novos — jogador com a quest recebe o item condicional, jogador
sem a quest não recebe nada, dois jogadores do MESMO grupo veem coisas
diferentes no MESMO corpse — o cenário exato da dúvida do usuário —,
reabrir não re-sorteia pro mesmo jogador, fluxo ponta-a-ponta via
`LOOT_REQUEST`/`LOOT_RESULT` credita item comum + condicional sem
duplicar). Confirmado via `git stash` que os 5 falham sem o fix.
`tests/test_session.py::TestPartyLootSync` (comportamento de itens
comuns/gold em grupo, pré-existente) e `tests/test_party.py`/
`tests/test_client_ui.py` continuam passando sem regressão. Suíte
completa rodada 3x.

**Fase M1 — Item de mapa saqueável**: planta/pergaminho/ferramenta que
abre o MESMO modal de loot de corpse de mob, sem dono/grupo (público —
qualquer jogador pode saquear), permanente (sem expirar até a Fase M2
trazer respawn de verdade).

**Histórico rápido** (mesmo dia, 25/07/2026, iterado 3x com o usuário
até chegar no design certo): 1ª versão tratava harvestable como um dict
solto em `self._corpses` (`owner_eid=-1`), sem colisão/Y-sort real — o
jogador atravessava por cima e o desenho ficava sempre atrás do player.
Depois ganhou `color` e um "sprite" via `assets/icons/`+`ICONS` (ícone
quadrado, convenção errada — usuário apontou que devia reaproveitar o
catálogo de SPRITES de objeto de mapa, `assets/tiles/`+`engine/tileset.py`).
Ao testar essa correção, o usuário levantou o problema de fundo: o
harvestable devia **ser** um objeto de mapa de verdade (colisão + Y-sort
automáticos, como árvore/caixa/barril) OU um NPC de serviço sem
combate/diálogo — a 2ª opção venceu por cobrir os dois casos ao mesmo
tempo (decoração fixa E algo "solto" tipo NPC) com o menor risco. Design
final abaixo — `color`/`icon_key` (das tentativas anteriores) foram
removidos do `Corpse`, sem uso.

**Design final: harvestable é uma entidade ECS real, sem combate/diálogo**

- **Componente `Harvestable`** (`engine/components.py`): só
  `corpse_id: int` — liga a entidade ao dict em
  `self._corpses[corpse_id]`, que continua sendo a fonte de verdade do
  loot (itens/moedas/`quest_rolls`/`no_decay` — Fase L1 inalterada).
- **`Renderable` ganha `sprite_id: str = ""`**: ID já catalogado em
  `engine/tileset.py` (`OBJECT_SHEET_TILE_MAP`/`SHEET_TILE_MAP`, ex.:
  `"pr_box1"` de `OBJECT_SHEET_FAMILIES["TX Props"]`). `RenderSystem.
  render()` (`ui/systems.py`) usa `ui.tile_sprite_manager.TILE_SPRITES.
  get_raw_sprite(sprite_id)` no lugar do retângulo colorido quando
  setado — ancorado igual a qualquer objeto de mapa (base do sprite =
  base do tile). `sprite_id=""` (default) preserva 100% o comportamento
  antigo pra qualquer outra entidade.
- **Fábrica** (`engine/entity_factory.py::create_harvestable_entity`):
  `Position` + `TileMovement` PARADA (current==target, sem
  Combatant/AIControlled/Faction) + `Renderable(sprite_id=...)` +
  `Harvestable(corpse_id=...)`. Reaproveitada tanto pelo SERVIDOR
  (`_create_harvestables_for_map`) quanto pelo CLIENTE
  (`_spawn_remote_harvestable`, mesmo padrão de `create_enemy`
  compartilhado por `_spawn_remote_mob`).
- **Colisão automática, sem código dedicado**: `TileValidationSystem`
  (`engine/world_systems.py`) já constrói seu cache de tiles ocupados
  iterando QUALQUER entidade com `TileMovement` (não filtra por
  `Combatant`) — uma entidade parada já bloqueia o tile pros outros
  players de graça.
- **Y-sort automático, sem código dedicado**: `RenderSystem.render()`
  já Y-sorta QUALQUER entidade com `(Position, Renderable)` junto com
  os objetos estáticos de mapa, na mesma lista ordenada por
  `foot_y = position.y + renderable.height/2`.
- **Descoberta/sync — reaproveita o sweep de mob, não duplica**:
  `self._harvestable_eids: set[int]` (WorldServer) é um set PRÓPRIO,
  separado de `_mob_eids` (que é filtrado por `Combatant` e usado por
  ~12 outros pontos do código que assumem `CombatStats` presentes —
  misturar quebraria isso). Só 2 pontos precisaram de 1 linha cada pra
  incluir harvestable no MESMO mecanismo genérico:
  - `server/session.py::_dispatch_tick_deltas`: o índice
    `_mob_positions`/`_mob_hash` (usado pelo sweep estacionário de
    `_build_update_for_session`) passa a iterar
    `_mob_eids | _harvestable_eids`.
  - `server/world_server.py::get_mobs_in_aoi` (usado pelo `WORLD_STATE`
    de login): mesma troca, `_mob_eids | _harvestable_eids`.
  - `get_entity_spawn_data`/`_build_mob_spawn_payload`: gate aceita
    `eid in _mob_eids OR eid in _harvestable_eids`; `_build_mob_spawn_payload`
    ganhou um branch bem no topo — se a entidade tem `Harvestable`,
    retorna um payload SIMPLES (`kind:"harvestable"`, `eid` REAL agora
    — não precisa mais do truque de eid negativo, já que é uma entidade
    de verdade —, `tx`, `ty`, `name`, `sprite_id`, `corpse_id`) sem
    passar pela lógica de combate/raça/tier.
  - O follow-up de `LOOT_AVAILABLE` personalizado (`_resolve_conditional_loot_for`,
    Fase L1) que já existia em `_dispatch_tick_deltas` e `_spawn_and_start`
    **não precisou mudar** — ele já checava `kind=="harvestable"` +
    lia `corpse_id` do payload, forma que se manteve idêntica.
  - `get_harvestables_in_aoi` (função dedicada da versão anterior) foi
    REMOVIDA — supersedida pelo `get_mobs_in_aoi` genérico acima.
- **Cliente** (`client/remote_entity_handlers.py::_spawn_remote_harvestable`,
  chamado por `_handle_msg_entity_spawn`/`_handle_msg_world_state`):
  cria a entidade local via a MESMA fábrica compartilhada + um `Corpse`
  vazio (`loot=[]`, `coins=0`) — isso é o que faz `LootSystem.
  _try_open_corpse`/`MouseTargetingSystem._corpse_at_world_pos`
  funcionarem SEM NENHUMA mudança (eles só olham `Position`+`Corpse`).
  `_handle_msg_loot_available`: se o `corpse_id` já é conhecido (entidade
  já existe, criada no spawn), ATUALIZA o `Corpse` já anexado em vez de
  criar uma entidade nova (fluxo de corpse de mob morto, ainda
  desconhecido nesse ponto, continua chamando `create_corpse` como
  sempre).
- **Fix da elipse duplicada** (o bug relatado pelo usuário: a marca de
  loot aparecia por cima da sprite): `LootSystem.render_world` e
  `client/remote_entity_handlers.py::_draw_remote_corpses` (dois
  desenhos de corpse INDEPENDENTES, um deles nem tinha sido tocado nas
  correções anteriores) ganharam um guard — pulam qualquer `Corpse` que
  TAMBÉM tenha `Renderable` (harvestable já é desenhado, Y-sorted, pelo
  `RenderSystem`). Corpse de mob morto nunca tem `Renderable`,
  comportamento antigo 100% intacto pra eles.
- **Caveat conhecido, não resolvido nesta fase**: sem Y-sort real
  contra o próprio player local no sentido de "quem pisou na frente de
  quem" ser recalculado quadro a quadro por profundidade dinâmica — o
  Y-sort aqui é o MESMO que já vale pra mob/NPC (compara `foot_y`, não
  há sistema de oclusão parcial). Suficiente pro pedido atual.

**Pra customizar o sprite de um harvestable**: usar um ID já catalogado
em `engine/tileset.py` (`OBJECT_SHEET_FAMILIES`, ex.: `"box1"`→`"pr_box1"`,
`"chest"`→`"pr_chest"`, `"barrel"`→`"pr_barrel"`, `"bush1"`→`"pl_bush1"`)
no campo `"sprite"` da entrada em `maps/map_X_entities.json`. Harvestable
de teste (`maps/map_1_entities.json`, 130/374): "Caixa de Teste (M1)",
`"sprite": "pr_box1"`, itens `training_sword` + `small_hp_potion ×3`,
10 moedas — **validado pelo usuário em jogo real** (print confirmando
loot batendo 100% com o JSON) antes da correção de sprite/colisão.

**Pedido do usuário, DEFERIDO para uma fase futura** (explicitamente
"guarde isso para uma próxima fase"): trocar a definição por-posição de
harvestable por uma definição de ZONA — centro + raio + quantidade, com
itens spawnando em posições aleatórias dentro da área (mesmo padrão de
`spawn_zones` de mob).

**Validado**: `tests/test_session.py::TestHarvestableM1` (9 testes —
`_create_harvestables_for_map` cria a entidade real com
`Harvestable`/`Renderable(sprite_id)`/`TileMovement` corretos e ignora
item_key inexistente, `_merge_entities_json` converte lista JSON em
tuple, colisão de verdade via `is_tile_walkable` antes/depois de criar o
harvestable, dois jogadores sem grupo saqueiam harvestables públicos
diferentes, `no_decay` nunca expira mesmo com timer finito, sweep de
tick E login descobrem a entidade via o mecanismo genérico
`_mob_eids | _harvestable_eids` sem duplicar lógica, `_handle_msg_world_state`
chama `_spawn_remote_harvestable` — nunca cai no branch player/mob),
`tests/test_client_ui.py` (+3 testes — `_spawn_remote_harvestable` cria
a entidade com sprite+`Corpse` vazio e é idempotente pro mesmo
server_eid, `LOOT_AVAILABLE` de harvestable já conhecido atualiza o
`Corpse` no lugar em vez de criar uma segunda entidade — confirmado
`len(get_entities_with(Corpse)) == 1`). Confirmado via `git stash` que
os testes de plumbing falham sem a implementação (o de resolução do
catálogo de sprite em si não depende do código novo, testa só a
precondição). Suíte completa (529 testes) rodada 3x, 0 falhas.

**Não validado nesta sessão**: aparência/colisão/Y-sort do harvestable
com a arquitetura de entidade real em jogo (a validação visual anterior
do usuário foi contra a versão de `Corpse.color`/`.sprite_id`, já
substituída) — precisa de nova confirmação visual.

**Bug real encontrado pelo usuário ao testar (25/07/2026)**: caixa
apareceu no mapa, mas clique direito não abria loot nenhum. Causa:
`client/network_handlers.py::_handle_msg_aoi_update` (que processa a
lista `"spawned"` de `AOI_UPDATE` — o caminho de descoberta MAIS COMUM,
quando o player anda até perto de algo já existente) tem seu PRÓPRIO
dispatch de `kind`, **separado** de `_handle_msg_entity_spawn`/
`_handle_msg_world_state` (que já tratavam `"harvestable"` certo desde
o commit anterior) — 3 call sites com dispatch de `kind` duplicado, só
2 foram corrigidos. Sem o branch aqui, o harvestable caía no loop de
fallback "player" (`kind not in ("enemy", "mob_projectile")`) e virava
um JOGADOR REMOTO FANTASMA — a entidade de verdade (com o `Corpse` que
o clique direito precisa) nunca era criada, então `LootSystem.
_try_open_corpse` nunca achava nada pra abrir. Fix: `kind == "harvestable"`
entra no primeiro loop (ao lado de `enemy`/`mob_projectile`, chamando
`_spawn_remote_harvestable`) e é excluído do loop de fallback.

**Validado**: `tests/test_client_ui.py::
test_aoi_update_spawned_harvestable_chama_spawn_remote_harvestable` —
reproduz o cenário exato (harvestable via `AOI_UPDATE.spawned`, confirma
que NÃO vira `_remote_players` e que a entidade real com `Harvestable`+
`Corpse` é criada). Confirmado via `git stash` que falha sem o fix
(reproduziu o bug relatado: `assert 99 not in _remote_players` falhava
de verdade). Suíte completa (530 testes) rodada 3x, 0 falhas.

**2º bug real encontrado pelo usuário ao testar de novo (25/07/2026)**:
com a caixa já descoberta (visível no mapa), clique direito "não fazia
nada" — nem abria o loot, nem dava outro feedback. Causa: a tolerância
de clique de corpse (`MouseTargetingSystem._corpse_at_world_pos` e
`LootSystem._try_open_corpse`, `ui/systems.py`) era um retângulo FIXO
pequeno (±14×±10px), calibrado pra elipse achatada de 20×12px que TODO
corpse usava antes do harvestable ganhar sprite real. `pr_box1` (32×64,
ancorado com a base no tile) sobe ~48px acima do centro da entidade —
a maior parte da área VISUALMENTE clicável (o topo/meio do sprite,
onde a maioria dos cliques naturalmente cai) ficava fora dessa
tolerância antiga, então clicar ali não achava nenhum candidato.

**Fix**: `ui/systems.py::_corpse_click_rect(world, entity_id, pos)`
(função nova, nível de módulo, compartilhada pelas duas classes) —
se o corpse tem `Renderable` com `sprite_id` setado, a área clicável
vira o retângulo REAL do sprite (mesmo ancoramento de
`RenderSystem.render()`: base do sprite = base do tile, largura/altura
de `TILE_SPRITES.get_raw_sprite()`); senão mantém a tolerância antiga
(±14×±10px) — corpse de mob morto nunca tem `Renderable`, então
comportamento antigo 100% intacto pra eles. Usado tanto em
`_corpse_at_world_pos` (decide se o clique é "loot" e não "andar até
o tile") quanto em `_try_open_corpse` (escaneia candidatos pra abrir o
modal).

**Validado**: 3 testes novos em `tests/test_client_ui.py` — clique no
TOPO visual do sprite alto (fora da tolerância antiga, dentro da altura
real de 64px) abre o modal via `_try_open_corpse` E via
`_corpse_at_world_pos`; corpse de mob morto (sem `Renderable`) NÃO
fica clicável a essa mesma distância, provando que a tolerância antiga
continua intacta pra ele. Confirmado via `git stash` que os 2 testes de
harvestable falham sem o fix (o de mob morto passa mesmo sem o fix, de
propósito — prova que é o comportamento ANTIGO, não algo que o fix
introduziu). Suíte completa (533 testes) rodada 3x, 0 falhas.

**3º bug real, achado PROATIVAMENTE (não relatado pelo usuário, mas
plausível o suficiente pra corrigir junto — o teste do usuário depois
funcionou via relogin antes de eu confirmar isso em jogo)**:
`LootSystem._try_open_corpse` (`ui/systems.py`), quando o clique acontece
de LONGE (>1 tile de distância — `dist > 1`), manda o player ANDAR até
o próprio tile do corpse (`player_auto.ground_target = (c_tile_x,
c_tile_y)`) antes de abrir o modal. Isso sempre funcionou pra corpse de
mob morto (sem colisão — o tile é andável). Harvestable AGORA tem
colisão real (`TileMovement`, Fase M1 revisão 2) — o PRÓPRIO tile do
harvestable é SÓLIDO, então um clique de longe mandava o player pra um
destino inalcançável: a fila de movimento nunca terminava, `dist` nunca
descia pra `<=1`, e o modal nunca abria — sem nenhum erro visível,
parecia só "não fez nada".

**Fix**: em vez de mirar o próprio tile do corpse, mira o tile ADJACENTE
mais próximo do player (mesmo padrão que `_walk_to_merchant`, linha
~2546, já usa pra NPC sólido — 4 direções cardeais, escolhe a de menor
distância Manhattan até o player). Corpse de mob morto ganha o mesmo
comportamento (andar até do lado em vez de por cima) — mudança cosmética
sem impacto real pra eles.

**Validado**: `tests/test_client_ui.py::
test_try_open_corpse_de_longe_anda_pro_tile_adjacente_nao_pro_proprio` —
clique de longe (dist=3) num harvestable confirma que `ground_target`
NUNCA é o próprio tile do harvestable, e É um dos tiles adjacentes mais
próximos do player. Confirmado via `git stash` que falha sem o fix
(reproduziu o bug: `ground_target == (3, 3)`, o próprio tile do
harvestable). Suíte completa (534 testes) rodada 3x, 0 falhas.

**4º bug real relatado pelo usuário (25/07/2026)**: looteou a caixa com
o personagem A (Anarin) — pegou o loot, caixa "sumiu" da TELA DELE. Logou
com outro personagem (B, Aventureiro), foi até o mesmo spot — a caixa
ainda estava lá (visível), mas vazia (sem loot). Colocando os dois
personagens perto do spot ao mesmo tempo: só B via a caixa; A não via
mais nada ali. Causa: `client/network_handlers.py::
_sync_local_corpse_after_take` (chamado por `_handle_msg_loot_result`,
disparado só pra quem de fato manda um `LOOT_REQUEST`) remove a
entidade LOCAL do `Corpse` sempre que ele esvazia — comportamento
CERTO pra corpse de mob morto (deveria mesmo desaparecer depois de
saqueado), errado pra harvestable (`no_decay=True` no servidor,
permanente por design). Só quem realmente esvaziou o pote (A) passa por
esse código — B, cujo próprio saque nunca chegou a pegar nada (o pote
comum já estava vazio quando ele descobriu a caixa), nunca disparou
`_handle_msg_loot_result`, então sua cópia local nunca foi removida —
daí a assimetria entre os dois clientes.

**Fix**: mesmo sinal já usado em `LootSystem.render_world`/
`_draw_remote_corpses` — se a entidade tem `Renderable` (harvestable),
`_sync_local_corpse_after_take` retorna sem remover nada quando o
corpse esvazia (só limpa o loot mesmo, a entidade/sprite continua na
tela, visível mas sem nada pra saquear). Corpse de mob morto (sem
`Renderable`) continua desaparecendo normalmente.

**Design SUGERIDO pelo usuário ao relatar o bug** (ainda não confirmado
explicitamente, aplicado por ser a leitura mais natural do pedido):
harvestable esvaziado não precisa sumir — pode continuar visível, só
sem loot, até a Fase M2 trazer respawn de verdade (loot volta depois de
um tempo). Loot de harvestable já era (antes deste fix) um POTE COMUM
público (não "uma cópia por jogador") — uma vez que alguém pega os
itens, acabou pra todo mundo até o respawn, mesmo princípio de
free-for-all já usado em corpse de mob morto em grupo, só que sem
exigir grupo (qualquer jogador pode saquear, `owner_eid=-1`) — esse
comportamento de pote comum não mudou, só a visibilidade da caixa vazia.

**Validado**: 2 testes novos em `tests/test_client_ui.py` — esvaziar um
harvestable (via `_handle_msg_loot_result`) NÃO remove a entidade local
nem tira do `_available_loot`; esvaziar um corpse de mob morto (sem
`Renderable`) CONTINUA removendo normalmente (regressão intacta).
Confirmado via `git stash` que o teste de harvestable falha sem o fix
(o de mob morto passa mesmo sem o fix, de propósito — prova que é
comportamento ANTIGO, não introduzido agora). Suíte completa (536
testes) rodada 3x, 0 falhas.

**Auditoria feita** (evitar mais rodadas de "achei outro"): busquei
TODOS os `world.remove_entity(...)` em `client/*.py`/`ui/systems.py`
que pudessem tocar um `Corpse` — só este e o de
`_handle_msg_entity_despawn` (`eid < 0`, exclusivo de corpse de mob
morto EXPIRADO server-side, nunca dispara pra harvestable porque seu
eid é sempre positivo — entidade real, nunca despachado como negativo)
mexem nisso. Os outros `remove_entity` do arquivo são de player/mob/
projétil remoto, sem relação com `Corpse`.

**Fase M2 — Respawn (harvestable de posição fixa, 25/07/2026)**: depois
que o usuário perguntou "como configurar trava de quest/respawn/
quantidade numa área", alinhamos o design via `AskUserQuestion` antes de
implementar (decisões abaixo, não perguntar de novo):
- Gatilho do respawn: só conta quando o pote esvazia POR COMPLETO
  (itens E moedas) — saque parcial não inicia o timer.
- Respawn reseta TUDO: itens/moedas comuns E `quest_rolls` (Fase L1) —
  todo mundo ganha uma chance nova, mesmo quem já tinha resolvido o
  sorteio condicional antes do respawn.

`server/world_server.py::_create_harvestables_for_map` agora guarda o
TEMPLATE original (`_template_items`/`_template_coins`, antes de
resolver via fábrica) + `respawn_s` (0/ausente = nunca reabastece,
comportamento da Fase M1 intacto) + `empty_timer: 0.0` no dict do
corpse. Resolução de item extraída pra
`_resolve_harvestable_items(template_items, context_name)` (reaproveitada
tanto na criação quanto no respawn, evita duplicar a lógica de fábrica).

Novo `WorldServer._tick_harvestable_respawn(dt)`, chamado do MESMO
lugar que já chama `_process_loot_drops(dt)` — NÃO virou uma `System`
ECS nova (harvestable não precisa de iteração genérica tipo
`AIControlled`, só bookkeeping de timer). Pra cada corpse com
`respawn_s > 0` vazio por completo, incrementa `empty_timer`; ao passar
de `respawn_s`, re-resolve os itens do template, zera `quest_rolls`,
zera o timer, e enfileira em `self._pending_harvestable_refill`
(mesmo padrão produtor/consumidor de `_pending_loot_notifications`/
`consume_loot_notifications()` — `WorldServer` não tem acesso a
`self._sessions`, só `SessionManager` tem).

`SessionManager` ganha `consume_harvestable_refills()` (espelha
`consume_loot_notifications`) + um bloco novo em `_dispatch_tick_deltas`
(logo após o de corpse/loot) que manda `LOOT_AVAILABLE` personalizado
(via `_resolve_conditional_loot_for`) pra quem já conhece a entidade,
via `_sessions_in_aoi` — sem `ENTITY_SPAWN` novo, a entidade já existe e
nunca foi despawnada. **`_on_tick`'s `has_pending` ganhou
`_pending_harvestable_refill`** — mesma classe de bug já documentada
nesta seção pra arena/grupo/duelo (buffer novo preso até atividade
alheia destravar, se esquecido aqui).

**Validado**: `tests/test_session.py::TestHarvestableRespawnM2` (4
testes — sem `respawn_s` nunca reabastece mesmo após centenas de ticks,
saque parcial não conta como vazio, reabastece só depois do
`respawn_s` com `quest_rolls` resetado, sessão que já conhece a
entidade recebe `LOOT_AVAILABLE` de novo ao reabastecer SEM nenhuma
outra atividade no tick — prova que o fix de `has_pending` funciona).
Confirmado via `git stash` que os 4 falham sem a implementação. Suíte
completa (540 testes) rodada 3x, 0 falhas.

**Não validado nesta sessão**: reabastecimento em jogo real (depende de
um harvestable de teste com `respawn_s` configurado no mapa — nenhum
foi adicionado ainda, é conteúdo/design do usuário).

**Fase M3 — Trava de quest (25/07/2026)**: decisão confirmada via
`AskUserQuestion` — item de mapa com `requires_quest` definido fica
TOTALMENTE invisível (nem chega no AOI) pra quem não tem a quest ativa,
mesmo princípio de `class_req`.

`Harvestable` (`engine/components.py`) ganha `requires_quest: str = ""`;
`create_harvestable_entity` (`engine/entity_factory.py`) ganha o parâmetro
homônimo, threading até a construção do componente.
`_create_harvestables_for_map` (`server/world_server.py`) passa
`h.get("requires_quest", "")` do JSON de mapa.

**Bug latente da Fase M2 achado de graça durante o M3**: `engine/
map_loader.py::_merge_entities_json` nunca copiava `respawn_s` do JSON
bruto pra dentro do dict que vira `spawn_points["harvestables"]` — a
Fase M2 funcionava nos testes porque os testes montam `spawn_points`
direto, sem passar por `map_loader.py`, mas um mapa real com
`respawn_s` configurado teria esse campo silenciosamente descartado
antes de chegar no servidor. Corrigido no mesmo commit: bloco de
harvestables em `_merge_entities_json` agora copia `"respawn_s"` E
`"requires_quest"`.

**Mecanismo central**: novo `WorldServer._harvestable_visible_to(eid,
viewer_eid) -> bool` — `True` pra qualquer entidade sem
`Harvestable.requires_quest` (inclusive mobs, que não têm o componente);
pra harvestable travado, confere `hv.requires_quest in ql.active` da
`QuestLog` do `viewer_eid` (`viewer_eid=-1` = sem filtro, usado por
call sites sem sessão em escopo, pra não quebrar chamadas existentes).

Dois pontos de uso:
- `get_mobs_in_aoi` (`server/world_server.py`) ganha parâmetro
  `viewer_eid: int = -1`, filtra candidatos antes de incluir no
  resultado — cobre o `WORLD_STATE` de login (`_spawn_and_start` em
  `server/session.py` agora passa `viewer_eid=eid`, o próprio player
  logando).
- Sweep genérico de tick (`_build_update_for_session`, `server/
  session.py`) — logo após o check de `in_aoi`, `continue` se
  `_harvestable_visible_to` recusar, ANTES de adicionar em
  `known_eids`. Isso é o que faz revelação automática funcionar: se o
  player aceita a quest depois de logado, o eid nunca entrou em
  `known_eids`, então o MESMO sweep genérico o descobre sozinho no
  tick seguinte — zero código extra de "revelar".

**Validado**: `tests/test_session.py::TestHarvestableQuestGateM3` (5
testes) — harvestable sem trava visível pra qualquer viewer; login sem
a quest não recebe nem no `WORLD_STATE` nem no sweep; login com a quest
ativa vê e saqueia normal; aceitar a quest depois de logado revela no
tick seguinte sem relogar; sweep de tick nunca revela pra quem não tem
a quest. Confirmado via `git stash` (arquivos-fonte do M3, mantendo o
teste fora do stash) que 4 dos 5 falham genuinamente sem a
implementação. **`test_login_com_quest_ativa_ve_harvestable_com_trava`
passa mesmo sem o fix** — mesma característica já documentada acima
pra `no_decay`/`sem_trava`: esse teste só prova "quem TEM a quest não é
bloqueado", propriedade que vale trivialmente mesmo SEM nenhuma trava
(sem trava nenhuma, todo mundo vê, quem tem a quest incluso) — ele não
tem poder de detectar a ausência do gate sozinho. Os outros 4 (em
especial o de login SEM a quest, e o de sweep de tick) são quem
realmente comprova que a trava existe e funciona. Suíte completa (545
testes) rodada 3x, 0 falhas.

**Não validado nesta sessão**: trava de quest em jogo real (depende de
um harvestable de teste com `requires_quest` configurado no mapa —
nenhum foi adicionado ainda, é conteúdo/design do usuário).

**Fase M4 — Item concede quest nova (25/07/2026)**: decisão confirmada
via `AskUserQuestion` — vale pra QUALQUER origem do item (corpse de mob
morto OU harvestable de mapa), mapa global simples, não escopado por
tipo de fonte.

Novo `content/quests_data.py::ITEM_GRANTS_QUEST: dict[str, str]` —
chave é o NOME DE EXIBIÇÃO do item (não `item_key`; o ponto de gancho
só tem o nome já serializado disponível ali, mesma convenção de
`_resolve_conditional_loot_for`/tabelas de loot), valor é o `qid` de
`QUESTS`. Vazio por padrão (comentado com um exemplo) — preenchimento é
conteúdo/design do usuário.

Gancho em `server/loot_processor.py::request_loot`, logo antes do
`return {"items": items, "coins": coins}`: se `items` não está vazio,
importa `ITEM_GRANTS_QUEST` e, se não vazio, resolve a `QuestLog` do
`player_eid` e, pra cada item retirado nesta chamada cujo nome bate com
uma chave do dict, chama `quest_logic.try_start(self.world, player_eid,
ql, qid)`. Nenhuma checagem de "já ativa/completa" precisa ser feita
aqui — `try_start` já retorna `False` sozinho nesses casos (mesmo
princípio que faz retirada repetida do mesmo item nunca reiniciar/
resetar progresso já em andamento).

**Validado**: `tests/test_session.py::TestItemGrantsQuestM4` (4 testes)
— saquear um item mapeado concede a quest; item não mapeado não concede
nada; retirar o mesmo item de novo (2º corpse) não reinicia/reseta
progresso já feito na quest; `owner_eid=-1` (harvestable de mapa,
público) dispara o gancho igual a corpse de mob morto, confirmando que
não é escopado por origem. Confirmado via `git stash` (arquivos-fonte
do M4, mantendo o teste fora do stash) que os 4 falham genuinamente sem
a implementação (erro de import de `ITEM_GRANTS_QUEST`, que ainda não
existiria). Suíte completa (549 testes) rodada 3x, 0 falhas.

**Não validado nesta sessão**: fluxo em jogo real (depende de um item
de fato mapeado em `ITEM_GRANTS_QUEST` — dict vazio por padrão, é
conteúdo/design do usuário preencher).

**Fase "Zona de itens" (25/07/2026)**: última fase da leva planejada —
decisões confirmadas via `AskUserQuestion`: (1) zona aceita MISTURA de
sub-tipos (ex.: 3 Cogumelos + 2 Arbustos na mesma zona), mesmo padrão de
`spawn_zones` de mob; (2) nó de zona esgotado SOME de verdade (ao
contrário do harvestable de posição fixa, que fica visível vazio até
reabastecer no mesmo lugar) e um novo nasce em posição ALEATÓRIA dentro
do raio — o usuário escolheu isso de propósito em vez do meu padrão
recomendado ("sempre no mesmo lugar"); (3) um cooldown só pra zona
inteira, não por sub-tipo.

Novo array `"harvestable_zones"` em `maps/map_N_entities.json`
(`engine/map_loader.py::_merge_entities_json`) — cada zona guarda sua
lista `spawns` de sub-tipos INTACTA (ao contrário de `spawn_zones`, que
achata cada sub-tipo numa entrada separada — aqui cada zona precisa
continuar como UMA unidade, pra sortear entre os sub-tipos ao repor um
slot vago).

Servidor (`server/world_server.py`) NÃO virou uma `System` ECS nova —
mesmo princípio de M2 (bookkeeping de timer chamado de `_tick()`, ao
lado de `_process_loot_drops`/`_tick_harvestable_respawn`), harvestable
não precisa de iteração genérica por frame tipo `AIControlled`:
- `_harvestable_zones: dict[zone_id, dict]` (metadados: centro, raio,
  cooldown, `requires_quest` — 1 valor pra zona inteira, herdado por
  QUALQUER nó nascido nela, reaproveitando `_harvestable_visible_to` do
  M3 sem nenhuma mudança) + `_harvestable_zone_active: dict[zone_id,
  dict[hid, subtype_idx]]` (nós vivos agora) + `_harvestable_zone_timers:
  dict[zone_id, list[[subtype_idx, timer]]]` (1 timer por slot vago).
- `_harvestable_hid_to_eid: dict[hid, eid]` — mapeamento novo (faltava;
  harvestable de posição fixa nunca precisou remover a própria entidade,
  então nunca precisou de um caminho hid→eid) usado só pra remover a
  entidade certa quando um nó de zona se esgota.
- `_create_harvestable_zones_for_map`: registra a zona + enfileira 1
  timer por slot (escalonado, mesmo truque de preenchimento inicial de
  `SpawnZoneSystem` — evita spike de criação). Nós em si só nascem no
  primeiro `_tick_harvestable_zones`, não no carregamento do mapa.
- `_tick_harvestable_zones(dt)`: (1) detecta nós ativos totalmente
  esgotados (itens E moedas) e os REMOVE — `world.remove_entity` +
  `self._despawned_this_tick.append(...)`, reaproveitando o pipeline
  GENÉRICO de despawn já despachado como `ENTITY_DESPAWN` pra quem
  conhece o eid (nenhum broadcast novo); (2) decrementa timers de slots
  vagos e, ao zerar, chama `_pick_harvestable_zone_tile` (adaptação de
  `SpawnZoneSystem._pick_tile` — mesma amostragem O(até 40 tentativas),
  resolvendo o tilemap do MAPA da zona via `_map_bundles` em vez de um
  `_svc` de sistema já registrado, porque esta chamada acontece FORA de
  qualquer `System`) e spawna via `_spawn_harvestable_zone_node` (mesmo
  esqueleto de `_create_harvestables_for_map`, mas pra um nó só,
  gravando `zone_id` no corpse). Nascimento de nó novo também não
  precisa de notificação dedicada — o sweep genérico (`_mob_eids |
  _harvestable_eids`, já existente desde o M1) descobre sozinho no
  próximo tick.

Cliente (`client/network_handlers.py::_handle_msg_entity_despawn`):
branch `eid >= 0` ganhou um passo novo — se o eid é um
`_remote_harvestables` conhecido, remove a entidade local E limpa
`_available_loot` (lido via `Harvestable.corpse_id` do componente local,
já que o dict de loot é indexado por corpse_id, não por eid) — sem isso,
o nó de zona ficaria pra sempre na tela do cliente, órfão do servidor
(harvestable de posição fixa NUNCA passa por este caminho — nunca é
despawnado, só esvazia).

**Validado**: `tests/test_session.py::TestHarvestableZone` (5 testes) —
registrar a zona já enfileira 1 timer por slot (3+2=5) sem spawnar nada
ainda; um tick com `dt` grande spawna todos os 5 nós respeitando a
mistura de sub-tipos (3 Cogumelo + 2 Arbusto); esgotar um nó remove a
entidade E dispara o despawn genérico; nó reaparece só depois do
cooldown, em posição NOVA (testado com `unittest.mock.patch` sobre
`_pick_harvestable_zone_tile` — prova determinística de que o respawn
chama uma amostragem FRESCA em vez de reusar `tx/ty` do nó removido,
evitando um teste flaky baseado em probabilidade); `requires_quest` da
zona é herdado por QUALQUER nó nascido nela. Mais
`tests/test_client_ui.py::test_entity_despawn_de_harvestable_remove_
entidade_local_e_available_loot` (1 teste) — despawn de harvestable
remove a entidade local e limpa `_available_loot`. Confirmado via `git
stash` (arquivos-fonte da zona: `client/network_handlers.py`,
`engine/map_loader.py`, `server/world_server.py`, mantendo os testes
fora do stash) que os 6 testes novos falham genuinamente sem a
implementação. Suíte completa (555 testes) rodada 3x, 0 falhas.

**Não validado nesta sessão**: fluxo em jogo real (nenhuma zona de
teste foi adicionada ao mapa real ainda — `"harvestable_zones"` é
conteúdo/design do usuário preencher, igual às fases anteriores).

Com esta fase, a leva completa (Q1 → Q2 → L1 → M1 → M2 → M3 → M4 →
Zona de itens) planejada em
`C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md` está
encerrada.

### §34.52 — Validação em jogo do M2: clique de longe podia mirar o
adjacente ERRADO (sólido) e travar o loot pra sempre (25/07/2026)

Usuário testou a Fase M2 configurando uma 2ª caixa de teste em
`(111,383)` (`respawn_s: 10`) e reportou: não conseguia lootear de
jeito nenhum, testou várias vezes, reiniciou o servidor, nada mudou.

**Investigação**: servidor estava correto (`_corpses` tinha o item
resolvido certinho — `bone_shield` existe em `item_table.py::ITEMS`,
sprite `pr_box2` existe no catálogo `TX Props`, o tile `(111,383)` em
si é caminhável). O bug estava no CLIENTE, no mesmo método já corrigido
uma vez nesta sessão (`LootSystem._try_open_corpse`, `ui/systems.py`):
o fix anterior (clique de longe mira o tile ADJACENTE mais próximo, não
o próprio tile do corpse — corpse agora é sólido) escolhia o adjacente
"geometricamente mais próximo do player" **sem checar se esse adjacente
era caminhável**. A caixa de teste M1 (130,374) por sorte tem os 4
vizinhos livres, então o bug nunca apareceu; a caixa M2 (111,383) tem
uma parede colada bem ao lado — dependendo de onde o player estava
parado, o adjacente "mais próximo" escolhido era justamente o sólido, e
o auto-move mirava um tile pra sempre inalcançável, travando o loot
pra sempre (mesma CLASSE de bug do fix anterior — tile-alvo sólido —
só que desta vez no adjacente escolhido, não no próprio corpse).

**Achado de bônus, NÃO corrigido nesta sessão** (fora de escopo do
relato, mas documentado pra não esquecer): `_walk_to_merchant`
(`ui/systems.py:2546`) tem a MESMA falha (escolhe o adjacente mais
próximo do NPC sem checar walkability) — não foi tocado porque nenhum
bug real foi relatado ali ainda; só entra na lista se o usuário topar
mexer ou algum dia reportar o mesmo sintoma pra mercador/treinador.

**Fix**: `_try_open_corpse` agora filtra os 4 candidatos adjacentes por
`is_tile_walkable(player_entity, tx, ty)` ANTES de escolher o mais
próximo — só cai nos 4 sem filtro se NENHUM dos 4 for caminhável (caso
raro, comportamento antigo como fallback). Guard de `KeyError` cobre o
caso de teste isolado sem `tile_validation` registrado em `_svc`
(mesmo padrão já usado por outros testes que fixam serviços globais).

**Validado**: `tests/test_client_ui.py::
test_try_open_corpse_de_longe_pula_adjacente_mais_proximo_se_ele_for_
solido` — registra um `tile_validation` fake que bloqueia só o
adjacente que seria escolhido por padrão, confirma que o fix pula pro
próximo candidato caminhável. Confirmado via `git stash` (só
`ui/systems.py`, teste fora do stash) que falha genuinamente sem o
fix. Suíte completa (554 testes) rodada 3x — 2 falhas SEM RELAÇÃO com
esta mudança (`TestServiceResolverGuard::
test_resolver_neutraliza_svc_no_mapa_errado`,
`TestCCGeneralizado::test_disoriented_nao_bloqueia_movimento_bruto_no_
servidor`): ambas assumem que o tile `(131,374)` é caminhável, mas o
edit em andamento do usuário em `map_1_terrain.csv` (grade/cerca nova)
tornou esse tile sólido — nenhuma relação com harvestable/loot,
sinalizado ao usuário, aguardando ele estabilizar o terreno.

**Atualização**: este fix era real (confirmado por teste + git stash),
mas o usuário testou de novo e o sintoma ORIGINAL continuou — a causa
raiz de verdade era outra, completamente diferente, documentada em
§34.53 logo abaixo. Este fix continua válido e necessário (cobre um
bug de verdade), só não era o culpado principal do relato.

### §34.53 — Causa raiz de verdade do M2: tela de seleção de personagem
descartava LOOT_AVAILABLE que chegasse colado no WORLD_STATE do login
(25/07/2026)

Depois do fix de §34.52, o usuário testou de novo (2 caixas perto do
spawn, `(113,383)` e `(113,385)`) e o sintoma persistiu: caixa
aparecia normal na tela, mas clicar — mesmo já adjacente/em cima —
nunca abria o loot. Investigação em 3 etapas, cada uma eliminando uma
camada:
1. Servidor: dados/protocolo 100% corretos — simulado via `fake_login`
   e confirmado que `WORLD_STATE`+`LOOT_AVAILABLE` chegam com os itens
   certos.
2. Prints de diagnóstico temporários (removidos depois) em
   `ui/systems.py::_try_open_corpse` mostraram: clique acerta o
   candidato certo, mas `corpse.loot=[]` SEMPRE, em toda tentativa —
   ou seja, o `LOOT_AVAILABLE` nunca populava o `Corpse` local, apesar
   da entidade renderizar normal (prova que `WORLD_STATE` chegou, só
   o loot que não).
3. Prints equivalentes no SERVIDOR (`server/session.py`, também
   removidos depois) confirmaram que o servidor **manda** os 2
   `LOOT_AVAILABLE` corretos, sem erro nenhum (`session.send()` já
   loga falha de envio, e nenhuma apareceu).

Com o envio confirmado e a recepção confirmada como nunca acontecendo,
a suspeita virou "mensagem se perde ENTRE o socket e o handler do
`GameEngine`" — e foi exatamente isso: `ui/char_creation_screen.py::
run_online` (tela de seleção de personagem, que roda ANTES do
`GameEngine` assumir) tem seu PRÓPRIO loop de `net.poll()`, drenando
mensagens enquanto aguarda `WORLD_STATE` pra liberar a entrada no jogo.
O código antigo, ao achar `WORLD_STATE` NO MEIO do `for` que itera a
leva de mensagens recebida, dava `return True` IMEDIATAMENTE — sem
terminar de examinar o RESTO da leva. Como o servidor manda
`LOOT_AVAILABLE` (uma por harvestable dentro do AOI de login) logo
"colado" no `WORLD_STATE`, na MESMA leva de `net.poll()`, essas
mensagens ficavam presas na lista local `msgs` e eram perdidas pra
sempre quando a função retornava — nunca chegavam a ser re-enfileiradas
em `net.inbox` pro `GameEngine` processar depois.

**Por que só apareceu agora**: só acontece quando o harvestable está
perto o bastante do spawn pra JÁ entrar no snapshot de login
(`get_mobs_in_aoi` dentro do raio de AOI). A caixa de teste ORIGINAL
(M1, longe do spawn) nunca disparava esse caminho — ela só era
descoberta bem depois, via sweep de tick, quando esta tela de seleção
já tinha fechado há muito tempo (por isso "funcionou" da primeira vez,
antes do usuário mover as caixas pra perto do spawn pra facilitar o
teste).

**Fix**: extraída a lógica de processamento da leva pra uma função
pura nova, `_drain_char_select_batch(msgs, pending_action, game_buffer,
chars, confirm_del_id)` — itera a leva INTEIRA sempre, `WORLD_STATE`
só marca a INTENÇÃO (`got_world_state=True`); quem decide re-enfileirar
e retornar é o CHAMADOR, depois que o `for` termina de examinar tudo.
Extração feita especificamente pra tornar isso testável isoladamente
(a tela inteira é um loop pygame interativo, não dá pra testar fim-a-
fim sem simular clique/render — a lógica de mensageria em si, sim).

**Validado**: `tests/test_client_ui.py` (4 testes novos) —
`_drain_char_select_batch` preserva TODAS as mensagens que vierem
depois do `WORLD_STATE` na mesma leva (o caso exato do bug: 2
`LOOT_AVAILABLE` coladas); sem `WORLD_STATE` na leva não sinaliza
pronto; `CHARACTER_ERROR` limpa o buffer e reseta `pending_action`
(comportamento antigo preservado); `DELETE_CHARACTER_OK` remove do
`chars` e sinaliza reset dos índices de confirmação. Confirmado via
`git stash` (só `ui/char_creation_screen.py`, testes fora do stash)
que os 4 falham genuinamente sem o fix (a função nem existia).
Suíte completa (558 testes) rodada 3x — mesmas 2 falhas de sempre,
sem relação (ver §34.52, terreno em edição do usuário).

**Não validado em jogo ainda**: a investigação partiu diretamente do
relato do usuário ("caixa aparece, clico, não abre nunca"), com
diagnóstico ao vivo (prints temporários client+server, já removidos)
confirmando a causa exata — mas o fix em si ainda não foi confirmado
por ele em jogo real, aguardando novo teste.

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
| G6 | Transições de mapa (tiles em `transitions` do JSON) não existem no servidor — servidor só carrega 1 mapa. Cliente tinha `_do_transition()` disparando online sem gate, causando desync total (entidades remotas removidas, server ainda no mapa antigo, MOVE rejeitados). **Mitigação**: gate `if not self._net` em `game.py` bloqueia a transição silenciosamente online. Implementação real: `ZONE_CHANGE_REQ` C→S → servidor valida tile, move player para ECS do mapa de destino → `ZONE_CHANGE` S→C + novo `WORLD_STATE`. Requer servidor multi-mapa (cada mapa com ECS próprio, AOI por mapa) | Cavernas inacessíveis online; crash de desync se player chegasse ao tile |

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
