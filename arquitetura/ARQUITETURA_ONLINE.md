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

### §34.54 — Esvaziar harvestable disparava despawn genérico assimétrico
entre clientes (25/07/2026)

Depois do fix de §34.53, o usuário testou de novo e reportou 2 sintomas
novos: (1) ao esvaziar a caixa, ela some da tela — inclusive de OUTROS
jogadores no AOI, não só de quem saqueou; andar pra longe e voltar (sem
relogar) não trazia de volta; só relogar restaurava; (2) depois de
relogar, quando o item com `respawn_s` reabastecia, em vez da caixa
aparecer de novo, aparecia a ELIPSE antiga.

**Investigação**: reproduzi passo a passo via script (spawn remoto →
LOOT_AVAILABLE → LOOT_RESULT → novo LOOT_AVAILABLE de reabastecimento)
usando as funções REAIS do cliente — o fluxo "esperado" (guard de
`Renderable` em `_sync_local_corpse_after_take`) se comportou
CORRETAMENTE nessa simulação isolada, o que descartou aquele código
como causa. Busquei então TODOS os pontos que mandam
`MsgType.ENTITY_DESPAWN` no servidor (3 call sites) e achei o
verdadeiro culpado: `server/session.py::_handle_loot_request` (dentro
do handler de `LOOT_REQUEST`, não em `WorldServer.request_loot`) — o
bloco "corpo só some pra AOI quando fica REALMENTE vazio" (adicionado
17/07/2026 pra um bug de grupo — sacar só o ouro não devia remover o
resto do loot da visão do grupo) manda um `ENTITY_DESPAWN` genérico
(eid NEGATIVO, `-corpse_id`) pra **qualquer** corpse que zere itens E
moedas — sem NUNCA checar se o corpse era um harvestable (`no_decay`)
ou um corpse de mob morto de verdade. Esse código é anterior a
harvestable virar entidade real (Fase M1) e nunca foi revisado depois.

O cliente, no handler desse eid negativo (`_handle_msg_entity_despawn`,
branch `eid < 0`), TAMBÉM nunca teve o guard de `Renderable` que os
outros caminhos de remoção já tinham (`_sync_local_corpse_after_take`,
`LootSystem.render_world`, `_draw_remote_corpses`) — removia a
entidade E limpava `_available_loot[corpse_id]` incondicionalmente.

Isso explica os dois sintomas: (1) qualquer jogador no AOI (não só quem
saqueou) recebia esse despawn e removia a entidade local de verdade —
por isso sumia "pra todo mundo" e não voltava andando perto (a
entidade estava genuinamente apagada do ECS local, só um WORLD_STATE
novo — via relog — recriava); (2) como `_available_loot[corpse_id]`
também era limpo, quando o reabastecimento (`consume_harvestable_
refills`) mandava um LOOT_AVAILABLE novo depois, `_handle_msg_loot_
available` não achava mais a entrada e caía no fallback antigo de
`create_corpse` — criando um Corpse SEM `Renderable`, desenhado como a
elipse velha por `LootSystem.render_world` (que só pula a elipse se a
entidade JÁ tiver `Renderable`).

**Nota importante (mudança de design, mesma conversa)**: o usuário
esclareceu que "sumir depois de lootear" NÃO é o problema em si — ele
QUER isso como opção (estilo WoW: nó de coleta some ao ser saqueado e
reaparece depois de um cooldown). O problema real relatado era só a
ASSIMETRIA entre clientes (sumir pra um e não pra outro) — este fix
resolve exatamente essa assimetria (o despawn agora é consistente:
NUNCA dispara pra harvestable `no_decay=True`, e quando disparar no
futuro via um parâmetro novo configurável, vai via o MESMO
`_sessions_in_aoi` broadcast, visto por todo mundo ao mesmo tempo).
Ver discussão de design (ainda em aberto) logo abaixo desta seção —
não implementado ainda, aguardando decisões do usuário.

**Fix**: `_handle_loot_request` só manda o despawn genérico se
`corpse_data.get("no_decay")` for falso (mob corpse). `_handle_msg_
entity_despawn`'s branch `eid<0` ganhou o MESMO guard de `Renderable`
dos outros caminhos, como segunda camada de defesa (mesmo se algum
broadcast futuro mandar despawn por engano pra um harvestable).

**Validado**: `tests/test_session.py::
TestHarvestableEmptyNaoDisparaDespawnGenerico` (2 testes) — harvestable
esvaziado não dispara `ENTITY_DESPAWN`; corpse de mob morto (sem
`no_decay`) continua disparando normalmente (regressão de 17/07/2026
intacta). `tests/test_client_ui.py` (2 testes) — `ENTITY_DESPAWN` de
eid negativo não remove harvestable (guard de `Renderable`); continua
removendo corpse de mob morto normalmente. Confirmado via `git stash`
(`server/session.py` + `client/network_handlers.py`, testes fora do
stash) que 3 dos 4 falham genuinamente sem o fix (o 4º, a regressão de
mob corpse, já passava antes — comportamento preexistente intacto, não
precisa falhar). Suíte completa (562 testes) rodada 3x — mesmas 2
falhas de sempre, sem relação (ver §34.52).

**Não validado em jogo ainda**: aguardando novo teste do usuário.

### §34.55 — Tag automática "Este item inicia uma quest" + primeiro item
de teste do M4 (25/07/2026)

Usuário pediu ajuda pra validar o M4 (item concede quest — ver §34.51):
queria um item genérico ("Pergaminho da Verdade", "Carta para Alfelio",
"Artefato Extremamente Misterioso" foram os exemplos dados) com nome +
uma tag "Este item inicia uma quest" + descrição de sabor entre aspas
no tooltip.

`Item` (`engine/components.py`) já tinha um campo `description` livre,
mas nada usava pra exibir a tag "inicia quest" — teria que ser escrita
à mão na `description` de cada item, arriscando ficar desatualizada se
o item saísse do `ITEM_GRANTS_QUEST` depois (ou vice-versa, esquecer de
marcar um item que já concede quest).

**Fix**: `ui/ui_helpers.py::item_tooltip_lines` agora checa
`item.name in ITEM_GRANTS_QUEST` (import local, mesmo padrão de
imports tardios do arquivo) logo após a linha de raridade — mesma
FONTE ÚNICA que o gancho real em `loot_processor.py::request_loot` usa,
sem duplicar a informação em dois lugares. `description` continua livre
pro texto de sabor (aspas incluídas no próprio texto, já que o campo
não formata nada sozinho).

Criado o primeiro item de teste real: `"Artefato Extremamente
Misterioso"` (`QUEST_ITEMS`, `item_type="material"`, `rarity="rare"`,
com a description exata que o usuário pediu) + uma quest de
VALIDAÇÃO simples (`"artefato_misterioso"`, objetivo genérico "falar
com qualquer mercador" — não exige NPC/turn-in novo no mapa, sem
compromisso com conteúdo final) + a entrada em `ITEM_GRANTS_QUEST`
ligando os dois.

**Bug de teste achado de graça**: `TestHarvestableZone` (Fase Zona de
itens, §34.51) ficou frágil ao conteúdo REAL do mapa assim que o
usuário adicionou sua primeira `harvestable_zones` de verdade
(`map_1_entities.json`, zona "Vômito") — `asyncSetUp` carrega o mapa
real via `make_session_manager()`, então a zona real passou a conviver
com a zona sintética de cada teste, quebrando
`test_no_reaparece_apos_cooldown_em_posicao_nova_nao_fixa_na_antiga`
(`mocked.assert_called_once()` via de repente 3 chamadas — a zona real
TAMBÉM tinha slot vago pra preencher). Fix: `asyncSetUp` agora limpa
`_harvestable_zones`/`_harvestable_zone_active`/`_harvestable_zone_
timers` logo após carregar o mapa, isolando os testes de qualquer
conteúdo real que o mapa venha a ter — mesmo princípio que os outros
testes desta classe já aplicavam sem perceber (contagens/asserts
implicitamente assumiam "mapa sem harvestable_zones", verdade só até
agora).

**Validado**: suíte completa (562 testes) rodada 3x, 0 falhas
relacionadas (mesmas 2 de sempre, ver §34.52). Tooltip verificado via
script standalone (linhas geradas na ordem certa: raridade → tag →
... → description entre aspas).

**Não validado em jogo ainda**: usuário ainda não testou saquear o
Artefato/ver o tooltip/receber a quest em jogo real.

### §34.56 — 3 bugs reais do primeiro teste completo da leva (M2/M3/Zona,
25/07/2026)

Usuário testou tudo (M2, Zona de itens, M3) numa rodada só e trouxe 3
achados — cada um com causa raiz PRÓPRIA, não relacionados entre si:

**1. Modal de loot parou de fechar sozinho ao esvaziar.** Efeito
colateral do fix de §34.54: o fechamento automático (`LootSystem.
update()`) só checava "componente `Corpse` sumiu" — que ERA verdade
pra harvestable ANTES daquele fix (a entidade era removida por engano
ao esvaziar, fechando o modal como acidente). Agora que harvestable
persiste corretamente (por design), o modal nunca tinha ganho uma
checagem própria de "esvaziou". Fix: `update()` agora fecha quando o
`Corpse` sumiu OU quando `not loot and coins<=0` — cobre harvestable
(persiste, mas fica vazio) e corpse de mob morto (ainda soma pela
remoção) nos dois casos.

**2. Colisão do harvestable ignorava a config real do catálogo do
sprite.** Usuário usou `pl_vomito` (`OBJECT_MAPPING['pl_vomito'].
is_solid == False` no catálogo — sprite decorativo, sem colisão) mas o
harvestable travava o tile de qualquer jeito. Causa: `create_
harvestable_entity` sempre adiciona `TileMovement` (necessário pra
posição/AOI — nunca pode ser omitido) mas `TileValidationSystem`
tratava QUALQUER `TileMovement` como ocupante do tile, sem exceção.
Fix: `Harvestable` ganha campo `solid: bool` (calculado 1x na criação,
via `OBJECT_MAPPING.get(sprite_id).is_solid`, default `True` se sprite
vazio/desconhecido — preserva o comportamento anterior pra quem não
tem entrada no catálogo); `TileValidationSystem.update()` pula do
cache de ocupados qualquer harvestable com `solid=False`. `TileMovement`
continua sempre presente — só a interpretação de "isso ocupa o tile"
mudou.

**3. Harvestable com trava de quest não sumia de novo ao completar a
quest.** A trava do M3 (§34.51) só cobria "revelar" — o sweep de
descoberta (`_build_update_for_session`) pula QUALQUER eid já em
`session.known_eids` (linha do `if mob_eid in session.known_eids:
continue`), então uma vez descoberto, o harvestable ficava visível PRA
SEMPRE, mesmo depois da quest sair de `QuestLog.active` (completada/
entregue). Fix, espelhando o padrão JÁ existente pra "Camuflagem"
(bloco "Mudanças de visibilidade" que re-avalia `_can_see()` pra quem
já é conhecido): novo sweep em `_build_update_for_session` — pra cada
eid em `session.known_eids ∩ gated_harvestable_eids` (harvestable com
`requires_quest` setado, pré-filtrado 1x por tick em `_dispatch_tick_
deltas` pra manter o custo baixo), reavalia `_harvestable_visible_to`;
se agora False, remove de `known_eids` e entra no `despawned` da
PRÓPRIA `AOI_UPDATE`. Cliente ganhou o cleanup correspondente
(`_handle_msg_aoi_update`'s despawned nunca tratava harvestable — só
`_remote_players`/`_remote_mobs` — ficava órfão em `_remote_
harvestables`/`_available_loot` pra sempre).

**Validado**: `tests/test_session.py` (2 testes) — sprite passável no
catálogo não trava o tile (regressão: sprite sólido continua travando,
teste já existente); completar a quest esconde o harvestable de novo
no tick seguinte (mesmo padrão de "atividade" que os outros testes de
revelação usam). `tests/test_client_ui.py` (3 testes) — modal fecha
sozinho ao esvaziar; modal NÃO fecha com loot restante (regressão);
`AOI_UPDATE` despawned remove harvestable local e limpa
`_available_loot`. Confirmado via `git stash` (`ui/systems.py`,
`engine/components.py`, `engine/entity_factory.py`, `engine/world_
systems.py`, `server/session.py`, `client/network_handlers.py`, testes
fora do stash) que os 4 testes NOVOS falham genuinamente sem os fixes.
Suíte completa (567 testes) rodada 3x — mesmas 2 falhas de sempre, sem
relação (ver §34.52).

**Item que ficou em aberto nesta leva, CONFIRMADO RESOLVIDO depois**:
usuário também relatou que a zona "Vômito" (`harvestable_zones`,
count=10) estava "floodando vários itens no mapa quando o respawn dá o
cd" — não foi achada uma causa concreta revisando `_tick_harvestable_
zones`/`_create_harvestable_zones_for_map` nesta leva (a lógica de
esgotar→enfileirar 1 timer de reposição→spawnar 1 substituto parecia
correta na leitura estática). Usuário confirmou depois (mesma rodada de
teste do §34.57) que o flood já não acontece mais. Causa raiz NUNCA
confirmada de forma isolada (sem reprodução dedicada) — hipótese mais
provável é efeito colateral do guard `not corpse_data.get("no_decay")`
no broadcast genérico de `ENTITY_DESPAWN` de corpse vazio (§34.54,
corrigido ANTES desta leva): se um nó de zona esgotado ainda disparasse
esse broadcast genérico de forma inconsistente, o cliente podia ficar
com uma sprite "fantasma" que nunca sumia mesmo com o nó já reposto no
servidor — cada CD subsequente somaria mais uma sprite por cima,
explicando o "flood". Não tratar como definitivo se o sintoma voltar.

**Validado em jogo**: usuário confirmou os 3 fixes (modal fecha
sozinho, colisão respeita o catálogo, trava de quest esconde de novo
ao completar).

### §34.57 — Fase M4 revisada: item concede quest vira decisão do jogador
(popup de aceitar/recusar), não automático (25/07/2026)

Usuário testou o M4 (Artefato Extremamente Misterioso) e pediu uma
mudança de design: em vez de a quest iniciar automaticamente ao
lootear (comportamento original do M4, §34.51), quis o padrão WoW-like
de "item de quest inerte": item vai pra bag ao ser saqueado, sem
efeito nenhum; clique direito nele abre um popup "Aceitar a quest
'X'?" com botões Aceitar/Recusar; recusar não descarta o item (fica na
bag, popup reabre no próximo clique direito); aceitar de fato inicia a
quest; quest completada consome o item ao entregar pro NPC (like
qualquer quest com objetivo `collect_item`); a quest vira requisito de
uma cadeia futura (`requires=` já resolve isso, nada novo aqui).

**Decisões confirmadas via `AskUserQuestion`**: popup reabre em TODO
clique direito enquanto a quest não for aceita (não só na primeira
vez); entrega continua via `turn_in_ids` no NPC do mapa (mecanismo já
existente, sem campo novo em `QuestDef`).

**Descoberta que reduziu o escopo**: `complete_quest` (`engine/
quest_logic.py`) já remove automaticamente itens de objetivos
`collect_item` cujo `loot_item` bate com o nome do item — a "entrega
consome o item" já existia, só a quest de teste usava `talk_to_npc`
em vez de `collect_item`. Trocado o objetivo de `"artefato_
misterioso"` pra `collect_item(loot_item="Artefato Extremamente
Misterioso")`.

**`QUEST_ACCEPT` já serve sem mudança nenhuma**: `server/session.py::
_handle_quest_accept` (usado hoje pelo diálogo de NPC) não exige
proximidade de NPC — só `quest_id` — então o popup do item manda a
MESMA mensagem, sem endpoint novo.

**Mudanças**:
- `server/loot_processor.py::request_loot` — REMOVIDO o gancho que
  chamava `try_start` automaticamente ao saquear (era o comportamento
  original do M4). `ITEM_GRANTS_QUEST` (`content/quests_data.py`)
  virou METADADO puro, consultado só no CLIENTE (tag do tooltip, já
  existia — e agora também o gatilho do popup).
- `client/inventory_handlers.py` — novo bloco "Item concede quest":
  `_try_open_item_quest_prompt(item)` (checa `ITEM_GRANTS_QUEST` +
  `QuestLog.active`/`.completed` do player; abre o popup só se a quest
  ainda não foi resolvida), `_item_quest_prompt_button_rects()`/
  `_draw_item_quest_prompt_ui()`/`_handle_item_quest_prompt_click()`
  espelhando EXATAMENTE o padrão já existente de convite de grupo
  (`client/party_handlers.py::_draw_party_invite_ui`/
  `_handle_party_click`) — mesmo layout, mesma mecânica de modal
  bloqueante. Clique direito num item na bag agora checa isso
  PRIMEIRO, antes de trade/consumir/equipar.
- `game.py` — wire-up: `_handle_item_quest_prompt_click` entra na
  MESMA cadeia de handlers bloqueantes de clique (logo após grupo),
  `_draw_item_quest_prompt_ui()` entra no mesmo bloco de desenho dos
  outros modais (após o convite de grupo).
- `engine/quest_logic.py::try_start` — novo pré-check pra objetivo
  `collect_item`: se o jogador ACEITA a quest depois de já ter o item
  na bag (o caso normal aqui — saqueou primeiro, decidiu aceitar
  depois), o objetivo nasce PRÉ-COMPLETO contando o que já está na
  Inventory (`min(count, quantidade_na_bag)`) — sem isso o objetivo
  nunca fecharia, já que o evento `"collect_item"` só dispara em
  pickups NOVOS (`quest_events.py`), não em itens já possuídos.
  Mesmo princípio que `reach_level`/`learn_skill` já usavam.

**Validado**: `tests/test_quest_logic.py` (3 testes) — `try_start`
pré-completa `collect_item` quando o item já está na bag; nasce
zerado sem o item; respeita `count > 1` (progresso parcial, não
estoura). `tests/test_client_ui.py` (6 testes) — popup abre pra item
registrado sem quest ativa/completa; não abre com quest já ativa; não
abre com quest já completa; não abre pra item não registrado; aceitar
manda `QUEST_ACCEPT` com o `quest_id` certo e fecha o popup; recusar
só fecha, sem mandar nada. `tests/test_session.py::
TestItemGrantsQuestM4` (reescrita, 4 testes) — saquear NÃO inicia a
quest sozinho (inverte o teste original do M4); `QUEST_ACCEPT`
(simulado via `try_start` direto) inicia com o objetivo já completo
(item simulado na Inventory via um "INV_SYNC" manual — `request_loot`
nunca tocou a Inventory do servidor, é client-authoritative pro item
em si, só o Wallet é servidor puro); item não mapeado não concede
nada; harvestable (owner_eid=-1) se comporta igual mob corpse (item
vai pra bag, quest não inicia sozinha). Confirmado via `git stash`
(`engine/quest_logic.py`, `server/loot_processor.py`, `client/
inventory_handlers.py`, `game.py`, testes fora do stash) que os 11
testes novos falham genuinamente sem os fixes (o `test_funciona_pra_
harvestable_tambem_nao_so_mob`/regressão do M4 original também mudou
de comportamento esperado, incluído na contagem). Suíte completa (576
testes) rodada 3x — mesmas 2 falhas de sempre, sem relação (§34.52).

**Não validado em jogo ainda**: usuário ainda não testou o popup em
jogo real.

### §34.58 — Correção do §34.57: popup custom vira reaproveitamento do
MESMO modal de quest do NPC (25/07/2026)

Usuário testou o popup do §34.57 em jogo real e REJEITOU a abordagem:
o popup custom (espelhando o convite de grupo) era um modal NOVO que
não existia antes — pedido explícito: "o correto é a estrutura da
quest seguir o padrão das outras, a única diferença é em vez de pegar
com um NPC, vc pega clicando com o direito no item". Ou seja, o clique
direito no item deve abrir o MESMO `QuestDialogSystem` (`ui/quest_
system.py`) que o diálogo de NPC já usa — mesmo layout, mesmos botões
Aceitar/Recusar, mesma renderização de descrição/objetivos/recompensa.
Como ao clicar direito o inventário está aberto, o pedido incluiu
fechar o inventário no mesmo gesto que abre o modal de quest.

**Por que não dava pra reaproveitar `QuestDialogSystem` sem mudança
nenhuma**: o sistema inteiro é construído em cima de um NPC real —
`is_open` é `self._dialog_npc_id != -1`; `render()` busca o componente
`QuestGiver` do NPC e SE FECHA SOZINHO se não achar (`giver is None:
self._close()`); o cabeçalho do painel mostra `NPC.name`; o clique de
Aceitar manda `QUEST_ACCEPT` com `npc_name=self._npc_name(dialog_npc_
id)` (dispara `talk_to_npc` no servidor). Não existe NPC nenhum no
fluxo de item.

**Fix — `open_for_item()` novo em `QuestDialogSystem`**: abre o modal
direto no estado `"detail"` (pula `"list"` — item sempre tem 1 quest
só) usando um eid sentinela (`-2`, nunca bate com nenhuma entidade real
— `world.get_component` com eid inexistente já retorna `None` de forma
seguro, confirmado em `engine/world.py::get_component`) e uma flag nova
`self._item_source: bool`. Mesma checagem de elegibilidade que o popup
antigo fazia (quest existe, não está ativa/completa). `render()` ganhou
um branch: se `_item_source`, pula o lookup de `QuestGiver`/`NPC` e usa
o NOME DO ITEM como cabeçalho (sem isso o `giver is None` fecharia o
modal no PRIMEIRO frame, já que não há NPC nenhum). O clique de Aceitar
manda `npc_name=""` quando `_item_source` (em vez do nome do NPC) —
sem isso, `_npc_name(-2)` cairia no fallback `"NPC"` e disparia
`talk_to_npc` pra um NPC fictício chamado "NPC" no servidor, efeito
colateral indesejado. `_close()` reseta as duas flags novas.

**`client/inventory_handlers.py`**: `_try_open_item_quest_prompt` (todo
o bloco do popup — property, botões, draw, click handler) REMOVIDO,
substituído por `_try_open_item_quest_dialog(item)`: consulta `ITEM_
GRANTS_QUEST`, chama `self._quest_dialog.open_for_item(qid, item.
name)`; se abriu, fecha o inventário (`_show_inventory = False`,
`_selected_inv_idx = -1`) — pedido explícito do usuário, já que o
inventário estava aberto quando o clique aconteceu.

**`game.py`**: removida a entrada de `_handle_item_quest_prompt_click`
na cadeia de handlers de clique bloqueante e a chamada de `_draw_item_
quest_prompt_ui()` no bloco de desenho — não existe mais um modal
próprio pra desenhar/clicar, o `QuestDialogSystem` já é desenhado/trata
eventos no lugar de sempre (mesmo código que atende o NPC).

**Validado**: `tests/test_client_ui.py` (10 testes) — `open_for_item`
abre em `"detail"` sem NPC real; não abre com quest já ativa/completa;
não abre com `qid` inexistente; `render()` não se autofecha (regressão
do bug que este fix corrige — sem o branch `_item_source`, o `giver is
None` fecharia sozinho); Aceitar manda `QUEST_ACCEPT` com `npc_
name=""`; Recusar só fecha, sem mandar nada; `_try_open_item_quest_
dialog` abre o modal E fecha o inventário; não abre pra item não
registrado (inventário intocado); não abre com quest já ativa
(inventário intocado). Confirmado via `git stash` (`ui/quest_system.
py`, `client/inventory_handlers.py`, `game.py`, testes fora do stash)
que os 10 testes novos falham genuinamente sem os fixes. Suíte completa
rodada 3x limpa — mesmas 2 falhas de sempre, sem relação (§34.52).

**Validado em jogo** (rodada de teste seguinte, ver §34.59): reaproveitar
o modal de NPC funcionou — cabeçalho, descrição/objetivo/recompensa,
Aceitar/Recusar e fechar o inventário no gatilho, tudo confirmado.
Restaram 3 bugs novos, não relacionados ao modal em si — ver §34.59.

### §34.59 — 3 bugs do playtest do modal reaproveitado (item não some ao
entregar, clique vazando pro minimap, Y-sort do harvestable, 28/07/2026)

Usuário validou o modal do §34.58 (itens 1, 3-7 da lista de validação, e
confirmou 9-10 do lote M1-M4/Zona) mas trouxe 3 bugs novos, cada um com
causa raiz PRÓPRIA:

**1. Item de quest entregue ao NPC não sumia da bag** ("Não consumiu o
item, ele continua na bag mas não tem nenhuma ação nele"). Causa:
`engine/quest_logic.py::complete_quest` sempre removeu o item de
objetivos `collect_item` do Inventory — mas SÓ do Inventory do MUNDO DO
SERVIDOR. O servidor nunca teve um canal pra avisar o CLIENTE dessa
remoção — `INVENTORY_UPDATE` (S→C) só existia pra CONCEDER item
(recompensa), nunca pra tirar um. Resultado: item removido no servidor,
mas "fantasma" pra sempre na bag local do cliente (que nunca soube da
remoção) — e como a quest já saiu de `ql.active`/entrou em
`ql.completed`, `open_for_item` (§34.58) passa a recusar reabrir o
modal nesse item, então o clique direito nele realmente não fazia mais
nada (efeito colateral do bug, não um bug à parte). Fix:
- `complete_quest` passa a retornar `(QuestReward, consumed)` em vez de
  só `QuestReward` — `consumed` é `[{"name": str, "stack": int}, ...]`,
  o que foi de fato removido (só objetivos `collect_item`).
- `server/session.py::_handle_quest_turn_in` manda esse `consumed` no
  MESMO `INVENTORY_UPDATE` que já manda os itens concedidos, campo novo
  `"removed"` — reaproveita a mensagem existente em vez de criar um
  `MsgType` novo (`shared/messages.py` documentado).
- `client/network_handlers.py::_handle_msg_inventory_update` ganha
  `_remove_items_from_inventory(removed)` — espelha a MESMA lógica de
  redução/pop que `complete_quest` já usa no servidor, na bag local.
- `ui/quest_system.py::QuestSystem._complete_quest` (caminho OFFLINE)
  só precisou ajustar o unpack (`reward, _consumed = ...`) — o
  `_consumed` é ignorado ali de propósito: offline, o Inventory mutado
  JÁ é o do jogador local, não existe cliente separado pra avisar.

**2. Clique direito no item de quest também movia o personagem pro
tile clicado** ("clicando em outros lugares dentro do modal do
inventário não acontece isso, então é um problema do item"). NÃO é bug
do item — é um bloco de código em `game.py::run()` que roda ANTES do
gating de modal: o clique no MINIMAP (`_minimap_click_consumed`,
detectado cedo pra ter prioridade) só checava `not self._map_overlay.
is_open`, nunca nenhum OUTRO modal (inventário, quest dialog, loja,
etc.). `screen_to_tile()` só confirma que o clique caiu dentro do
RETÂNGULO do minimap na tela — não sabe nem importa que um painel está
desenhado por cima cobrindo aquele canto. Item específico só evidenciou
o bug porque `_try_open_item_quest_dialog` fecha o inventário (`_show_
inventory = False`) no mesmo gesto — sem NENHUM modal aberto ao fim do
frame (nem inventário, nem, aparentemente, outro), o gating de
`systems_events` mais abaixo (que já bloqueia esse tipo de vazamento
pra QUALQUER modal) não pega esse bloco específico, que roda ANTES
dele. Mesma classe de bug de "atualização não-coesa" já documentada no
projeto (gating introduzido em um lugar nunca propagado pra outro
código correlato). Fix: bloco do minimap passa a checar `not self.
_any_modal_open()` (`client/modal_stack_handlers.py`, o mesmo ponto
único de verdade já usado pelo gating de `systems_events` logo abaixo)
em vez de só `_map_overlay.is_open`.

**3. Harvestable com sprite desenhava por cima do personagem/mob/NPC
no MESMO tile** ("o item fica sobre o personagem, e o personagem
deveria ficar sobre o item quando ele não tem colisão... também para
mobs e npcs"). Causa: `RenderSystem.render()` Y-sorta TUDO por
`foot_y = position.y + renderable.height/2`. Harvestable
(`create_harvestable_entity`) tem `Position.y` já no CENTRO do tile e
`Renderable.height=32` (tile inteiro) — `foot_y` cai então na BASE do
tile. Personagem/mob (sprite menor, ex. `PLAYER_SIZE=24`) no MESMO
tile tem `foot_y` mais alto na tela mas NUMERICAMENTE MENOR (mais perto
do centro do tile) — sorted ANTES do harvestable, logo desenhado por
baixo dele. Objeto ESTÁTICO de mapa (árvore/arbusto) nunca teve esse
problema porque usa outra convenção (`TileRenderSystem`: `sort_y =
ry*tile_size + tile_size//2`, CENTRO do tile, não a base) — o
harvestable copiou a fórmula de ANCORAGEM VISUAL dos objetos estáticos
(correta) mas não a fórmula de ORDENAÇÃO (ficou com o `foot_y` genérico
de entidade). Fix: entidade com `Renderable.sprite_id` setado (só
harvestable, por ora) ordena pelo CENTRO do tile (`position.y` puro,
já é exatamente isso por construção) em vez do `foot_y` genérico —
outras entidades (sem `sprite_id`) não mudam de comportamento.

**Validado**: `tests/test_quest_turn_in.py` (2 testes, classe
`TestQuestTurnInConsumesCollectItem`) — entrega remove do Inventory do
servidor E manda `removed` certo no `INVENTORY_UPDATE`; funciona junto
com `reward.items` no mesmo envio. `tests/test_client_ui.py` (3 testes
de `INVENTORY_UPDATE.removed` — remove item inteiro, reduz stack
parcial, item não presente não quebra; 2 testes de `_any_modal_open()`
confirmando que reconhece `quest_dialog` aberto via item mesmo com
inventário já fechado — mesmo estado do bug 2, ingrediente do fix, já
que o bloco em si é código inline no `run()` monolítico, não testável
isolado; 1 teste de Y-sort confirmando que harvestable com sprite
desenha ANTES do personagem no mesmo tile). `tests/test_server.py`
(1 teste existente atualizado pro novo retorno em tupla de
`complete_quest`). Confirmado via `git stash` (`engine/quest_logic.py`,
`server/session.py`, `client/network_handlers.py`, `ui/quest_system.py`,
`game.py`, `ui/systems.py`, `shared/messages.py`, testes fora do stash)
que os 6 testes novos/atualizados falham genuinamente sem os fixes
(inclusive um `ValueError: too many values to unpack` real ao tentar
desempacotar `QuestReward` — NamedTuple com mais de 2 campos — como
`(reward, consumed)` contra a versão antiga de `complete_quest`). Suíte
completa (590 testes) rodada 3x limpa — mesmas 2 falhas de sempre, sem
relação (§34.52).

**Validado em jogo**: usuário confirmou os 3 fixes (item consumido ao
entregar, clique no item não move mais o personagem, harvestable não
desenha mais por cima de personagem/mob/NPC no mesmo tile).

### §34.60 — Zona "Vômito": items da JSON como string solta em vez de
lista (28/07/2026)

Usuário adicionou `"Vômito de Zumbi"` em `QUEST_ITEMS` e configurou
como loot da zona "Vômito" (`harvestable_zones`) — servidor devolveu um
warning por CARACTERE (`item_key 'V' de 'Vômito' não existe...`,
`item_key 'ô' de 'Vômito'...`, etc.). Causa: `maps/map_1_entities.json`
tinha `"items": "Vômito de Zumbi"` (string solta) em vez de `"items":
["Vômito de Zumbi"]` (lista) — `_resolve_harvestable_items` (`server/
world_server.py:768`) faz `for entry in template_items:`, e iterar uma
STRING em Python itera caractere por caractere. Não é bug de código,
é erro de dado — corrigido só o JSON (envolver em lista).

Aproveitado pra esclarecer uma dúvida do usuário: cogitou migrar
`QUEST_ITEMS` pra dentro de `item_table.py` (catálogo único físico) ou
"corrigir" harvestable/harvestable_zones pra também olhar
`QUEST_ITEMS`. Nenhuma das duas é necessária — `resolve_reward_item_
factory()` (`engine/quest_logic.py`), usado por QUALQUER resolução de
`item_key` (recompensa de quest, harvestable de posição fixa,
harvestable de zona — `_resolve_harvestable_items` chama essa MESMA
função), já busca em `item_table.py::ITEMS` primeiro e cai pra
`QUEST_ITEMS` como fallback, sempre, em todo lugar — os dois dicts já
são um catálogo lógico único do ponto de vista de quem consome; a
separação em arquivos é só organizacional. Recomendado manter como
está.

**Validado em jogo**: usuário confirmou que a zona "Vômito" gera
"Vômito de Zumbi" corretamente após o fix do JSON. `"Vômito de Zumbi"`
em `QUEST_ITEMS`/a zona "Vômito" no mapa eram só um teste do usuário —
a pedido dele, não commitados (`content/quests_data.py`/`maps/
map_1_entities.json` continuam fora do commit desta seção, só o
aprendizado documentado aqui).

### §34.61 — Perseguição de combate "brigava" com movimento manual
(28/07/2026)

Usuário relatou: em combate melee (guerreiro), o personagem persegue o
alvo automaticamente — mas isso sobrescrevia QUALQUER tentativa de
movimento manual (WASD), fazendo o personagem "brigar" com o input do
jogador e voltar sozinho pro alvo. Pedido: apertar Espaço/skill não-AoE
deve persegue de verdade (já funcionava); mover manualmente (WASD ou
clique no chão) deve DESLIGAR a perseguição sem perder o alvo
selecionado nem sair de combate — se o mob alcançar o player de novo, o
ataque volta a acontecer sozinho.

**Investigação**: clique no chão (`MouseTargetingSystem.update()`,
`ui/systems.py`) já fazia exatamente isso — `combat_state.is_pursuing
= False` ao clicar num tile vazio, mantendo `target_entity_id`. WASD
(`PlayerInputSystem.update()`) fazia o oposto DE PROPÓSITO: um
comentário datado de 15/07/2026 documentava que o teclado preservava
`is_pursuing` de propósito, generalizado de uma exceção que antes só
existia pro arqueiro (`can_kite`) — motivo: sem isso, guerreiro perdia
o auto-attack ao se mover, tinha que re-clicar o alvo.

Perguntado ao usuário se essa mudança devia valer só pra melee
(preservando o kite do arqueiro, que depende de `is_pursuing=True`
continuar ligado — servidor exige isso pra ranged de verdade disparar,
`server/combat_processor.py`) ou pra todas as classes. Resposta:
nenhuma das duas opções apresentadas capturava a intenção — o usuário
não está pedindo pra mudar/quebrar o kite do arqueiro (que já funciona
do jeito que ele quer, "andar enquanto ataca"), só quer que a
PERSEGUIÇÃO (auto-move puxando o personagem de volta pro alvo) pare de
brigar com o movimento manual, pra QUALQUER classe (inclusive arqueiro
com arma melee equipada, que ataca de perto igual guerreiro). Uma vez
que `is_pursuing=False` só desliga o auto-move de perseguição local
(`_process_target`) — o servidor NUNCA exigiu `is_pursuing` pra golpe
corpo-a-corpo (só ranged) — desligar isso ao mover não impede o ataque
melee de retomar sozinho quando o alvo volta a ficar adjacente; a
distância é o único critério real pro servidor.

**Fix (1ª versão, ERRADA — ver §34.62 para a correção no mesmo dia)**:
bloco de movimento por teclado (`PlayerInputSystem.update()`, ~linha
630) ganhou `combat_state.is_pursuing = False` — mesma semântica que o
clique no chão já usava, agora espelhada no teclado.

**Validado**: `tests/test_client_ui.py` (2 testes) — WASD cancela
`is_pursuing` mas mantém `target_entity_id`. Suíte completa (592
testes) rodada 3x limpa — mesmas 2 falhas de sempre, sem relação
(§34.52).

**Corrigido no mesmo dia — ver §34.62**: usuário testou e reportou que
isso quebrava o combate inteiro (precisava reengajar manualmente
sempre que o alvo voltava ao alcance) — a premissa "servidor nunca
exige is_pursuing pra melee" estava certa, mas incompleta: havia uma
peça do fluxo (`_sync_combat_target`, ver §34.62) que este parágrafo
não tinha mapeado.

### §34.62 — Correção do §34.61: `is_pursuing` NUNCA deve ser desligado
por movimento manual — perseguição precisa de um sinal PRÓPRIO
(28/07/2026)

Usuário testou o fix do §34.61 e reportou: "o is_pursuing = False,
porém também quebra o combate — se eu ando, quando o mob fica no meu
alcance do personagem, ele não o ataca, eu preciso apertar a tecla
espaço ou clicar com o direito novamente". Esclareceu a intenção real:
"andar com o personagem não deve cancelar o auto attack, quando as
condições do auto attack forem cumpridas, o personagem deve atacar —
é o mesmo comportamento do arqueiro" (que já anda enquanto atira, sem
nenhuma exceção de classe — inclusive um arqueiro com arma MELEE
equipada, que ataca de perto igual guerreiro, se encaixa na mesma
regra).

**Causa raiz de verdade (não mapeada em §34.61)**: `is_pursuing` é lido
em TRÊS lugares com sentidos diferentes, não só "está perseguindo":
1. Chase — `_process_target`/`_process_archer_combat` só chamam
   `_auto_move_step` (auto-walk de volta ao alvo) se `is_pursuing`.
2. Gate de ataque RANGED — `server/combat_processor.py` exige
   `is_pursuing=True` pra disparar (melee nunca exigiu, só distância).
3. **Sincronização de rede** — `client/remote_entity_handlers.py::
   _sync_combat_target()` roda TODO frame e manda `AUTO_ATTACK{tid:
   local_target if is_pursuing else -1}` ao servidor (com 3 frames de
   grace). Ou seja: `is_pursuing=False` no cliente vira, depois do
   grace, um `AUTO_ATTACK{tid:-1}` de verdade — e
   `server/session.py::_handle_auto_attack` chama
   `WorldServer.set_player_target(session_id, -1)`, que LIMPA o
   `target_entity_id` NO SERVIDOR por completo. Desligar `is_pursuing`
   não é "só parar de perseguir localmente" — é literalmente
   desengajar o combate no servidor, exigindo reengajar (Espaço/
   clique) do zero. O clique no chão (`MouseTargetingSystem`) SEMPRE
   teve esse mesmo problema (nunca foi testado nesse cenário exato
   antes) — não é exclusivo do teclado.

**Fix de verdade**: `is_pursuing` NUNCA é tocado por movimento manual
(nem teclado nem clique no chão) — fica sempre como estava (ligado,
enquanto o combate durar). Em vez disso, `_process_target`/
`_process_archer_combat` ganharam um parâmetro novo, `suppress_chase:
bool`, que bloqueia SÓ as chamadas de `_auto_move_step` — nunca o
ataque, nunca `is_pursuing`, nunca `target_entity_id`. `PlayerInputSystem.
update()` calcula `_manual_move_wanted` (teclado segurado OU destino de
chão ativo OU "Seguir" pendente) uma vez por entidade e passa como
`suppress_chase`. Ataque continua dependendo só de alcance+cooldown
(melee) ou alcance+is_pursuing+aljava (ranged) — nada mudou aí, porque
`is_pursuing` nunca desliga.

**Mudanças**:
- `ui/systems.py::PlayerInputSystem.update()` — bloco de teclado
  reverte a mudança do §34.61 (não mexe mais em `is_pursuing`); novo
  cálculo de `_manual_move_wanted`, passado como `suppress_chase` pra
  `_process_target`. `_process_ground_move`/`_process_follow` deixam
  de exigir `not combat_state.is_pursuing` pra rodar (antes disso NUNCA
  rodariam mais, já que `is_pursuing` deixou de virar False) — rodam
  sempre que há destino de chão/"Seguir" pendente, já que a disputa
  pelo mesmo tick contra o chase é resolvida via `suppress_chase`.
- `_process_target`/`_process_archer_combat` — parâmetro novo
  `suppress_chase`, checado nas 3 chamadas de `_auto_move_step`
  (guerreiro/mago adjacente-fallback, mago fora de alcance de skill,
  arqueiro fora de `bow_range`).
- `MouseTargetingSystem.update()` (clique no chão) — reverte
  `is_pursuing = False` — `ground_target` sozinho já basta pra suprimir
  o chase via `_manual_move_wanted`.

**Validado**: `tests/test_client_ui.py` — reescritos os 2 testes do
§34.61 (agora provam que `is_pursuing`/`target_entity_id` NUNCA mudam
com WASD) + 2 testes novos direto em `_process_target` (`suppress_
chase=True` não inicia `_auto_move_step`/movimento; `suppress_
chase=False` continua perseguindo normalmente — regressão). Confirmado
via `git stash` (`ui/systems.py`, testes fora do stash) que os 3 testes
falham genuinamente contra a versão do §34.61 (um deles nem compilava —
`TypeError: unexpected keyword argument 'suppress_chase'`, prova de que
o parâmetro é novo de verdade). Suíte completa (593 testes) rodada 3x
limpa — mesmas 2 falhas de sempre, sem relação (§34.52).

**Corrigido no mesmo dia — ver §34.63**: usuário testou e reportou que
a perseguição voltava assim que soltava as teclas (o `suppress_chase`
era TRANSIENTE — só True enquanto a tecla estava fisicamente
pressionada NAQUELE frame — e o padrão real de movimento em jogo de
grade é toque curto por tile, não segurar continuamente). Esclareceu
que a intenção nunca foi "suprimir enquanto anda", e sim "desligar de
vez até eu reengajar de propósito".

### §34.63 — Correção do §34.62: perseguição precisa ficar desligada
até reengajamento de propósito, não só "enquanto anda" (28/07/2026)

Usuário testou o §34.62 e reportou: "ele voltou a perseguir o alvo
mesmo quando eu ando" — pediu pra eu explicar exatamente o que mudei
antes de tentar de novo (não adivinhar uma 3ª vez) e perguntar quando
precisasse de ajuda em vez de ficar em loop. Perguntei se ele segurava
a tecla continuamente ou tocava por tile; a resposta reformulou o
pedido por completo: **não quer que soltar as teclas reative a
perseguição sozinha** — quer que ela fique desligada até um
reengajamento de propósito (clique direito no alvo, Espaço, ou skill),
mesmo com o alvo parado ao alcance (nesse caso o auto-attack dispara
normalmente, só não persegue).

**Causa do §34.62 não bastar**: `suppress_chase` era um parâmetro
TRANSIENTE, recalculado do zero a cada tick a partir do estado
INSTANTÂNEO do teclado (`keys[...]`). Num jogo de movimento em grade,
o padrão comum é toque curto por tile (não segurar) — no intervalo
entre um toque e o próximo, `suppress_chase` voltava a `False`
imediatamente, reativando a perseguição por 1 tick a cada gap. Testado
e confirmado que segurar a tecla sem soltar TAMBÉM não bastava pro que
o usuário queria — porque nunca foi sobre segurar/soltar, e sim sobre
"desligar até eu decidir religar".

**Fix de verdade**: `suppress_chase` (parâmetro transiente) vira
`CombatState.chase_suppressed` (`engine/components.py`) — campo bool
STICKY, 100% client-side (nunca lido no servidor/rede, ao contrário de
`is_pursuing`). Movimento manual (WASD ou clique no chão) liga
`chase_suppressed = True` e ele PERMANECE True até um reengajamento de
propósito. Todo ponto que já setava `is_pursuing = True` (a real fonte
de verdade de "o jogador quer atacar este alvo") ganhou, na MESMA
linha, `chase_suppressed = False` — 8 lugares ao todo: clique direito
no alvo (`MouseTargetingSystem`), Espaço offline (`PlayerInputSystem.
_space_engage`) e online (`client/save_sync_handlers.py::
_space_engage_online`), skill instantânea e CAST_SKILL online
(`PlayerInputSystem`, 2 pontos), skill offline pós-cast
(`PlayerInputSystem`), Bola de Fogo (`ui/skill_handlers.py`) e
conclusão de cast (`ui/spell_system.py`). `_process_target`/
`_process_archer_combat` passam a ler `combat_state.chase_suppressed`
direto (não é mais parâmetro passado) nas 3 chamadas de
`_auto_move_step` — nunca o ataque em si, que continua disparando só
por alcance+cooldown (melee) ou alcance+is_pursuing+aljava (ranged),
sem nenhuma mudança nessa parte.

**Validado**: `tests/test_client_ui.py` (5 testes) — WASD liga
`chase_suppressed` sem tocar `is_pursuing`/alvo; `chase_suppressed`
continua `True` mesmo depois de soltar as teclas (o teste central desta
correção); `_process_target` com `chase_suppressed=True` não inicia
`_auto_move_step`; sem ele, persegue normalmente (regressão); Espaço
reengaja e zera `chase_suppressed` mesmo tendo sido ligado antes.
Confirmado via `git stash` (`engine/components.py`, `ui/systems.py`,
`ui/skill_handlers.py`, `ui/spell_system.py`, `client/
save_sync_handlers.py`, testes fora do stash) que os 4 testes novos
falham genuinamente contra a versão do §34.62 (um com
`AttributeError: chase_suppressed` — prova de que o campo é novo de
verdade). Suíte completa (595 testes) rodada 3x limpa — mesmas 2
falhas de sempre, sem relação (§34.52).

**Validado em jogo pelo usuário**: "Funcionou." — confirmado antes da
suíte rodar, a pedido explícito do usuário (suíte é demorada; ele quis
validar manualmente primeiro e só autorizar a rodada de testes depois
de confirmar em jogo real — mudança de processo pro resto da sessão).

### §34.64 — Contador de stack: esconde em "1", sem prefixo "x", outline
preto (28/07/2026)

Pedido simples do usuário: itens empilháveis com só 1 unidade não
precisam mostrar contador nenhum (só a partir de 2 de verdade
empilhadas); quando mostra, sem o prefixo "x" (só o número); e com
outline preto pra ficar legível sobre qualquer fundo.

**Fix**: `ui/ui_helpers.py::draw_stack_count` — fonte ÚNICA já
reaproveitada por inventário (`client/inventory_handlers.py`), barra de
consumíveis (`client/consumable_bar_handlers.py`), trade (`client/
trade_handlers.py`) e crafting (`ui/crafting_system.py`), então um fix
aqui cobriu todos os lugares de uma vez, sem precisar tocar nos
call-sites. Mudanças:
- Early-return novo: `if stack <= 1: return` (além do `max_stack <= 1`
  que já existia) — antes só checava se o item ERA stackável, não
  quantas unidades tinha AGORA, então um item stackável com só 1
  unidade mostrava "x1" à toa.
- Texto vira `str(stack)` em vez de `f"x{stack}"`.
- A "sombra" antiga (1 blit deslocado 1px, só cobria 1 canto) virou
  outline de verdade — texto preto desenhado nas 8 direções ao redor
  do texto branco.

**Validado**: `tests/test_client_ui.py` (3 testes) — stack=1 não
desenha nada (nem chama `font.render`); item não-stackável (`max_stack
=1`) continua sem desenhar nada (regressão); stack=3 desenha só "3"
(sem "x") com 9 blits (8 de outline + 1 do número). `pygame.Surface`
real não permite monkeypatch de `.blit` (atributo read-only, objeto
C) — os testes usam um `_SpySurf` fake com só o método necessário em
vez de espiar uma Surface de verdade. Confirmado via `git stash`
(`ui/ui_helpers.py`, testes fora do stash) que 2 dos 3 testes falham
genuinamente sem o fix (o terceiro, item não-stackável, já era
regressão do comportamento antigo — continua passando dos dois lados,
como esperado). Suíte completa (598 testes) rodada 3x limpa — mesmas 2
falhas de sempre, sem relação (§34.52).

**Validado em jogo pelo usuário**: "Funcionou." — confirmado antes da
suíte rodar (mesmo processo do §34.63).

### §34.65 — Corpo sumia com item de quest pendente no pote pessoal
(28/07/2026)

Usuário relatou (com pedido explícito de investigar e reportar ANTES
de mexer no código): com a quest "Veneno Mortal" ativa, matar uma
aranha com o arqueiro (talento Reciclagem — 80% de chance de recuperar
flechas, que entram no loot) e saquear só as flechas fazia o modal de
loot fechar sozinho e o corpo sumir — o "Veneno de Aranha" (item da
quest) nunca chegava a ser saqueado.

**Causa raiz**: o corpo guarda dois potes de loot separados —
`items` (comum, compartilhado por todo mundo) e `quest_rolls`
(pessoal, por jogador — Fase L1, `_resolve_conditional_loot_for`,
25/07/2026). `server/session.py::_handle_loot_request`'s checagem de
"o corpo ficou REALMENTE vazio, pode sumir da AOI de todo mundo"
(`_still_has_loot`) só olhava `items`/`coins` — nunca `quest_rolls`.
Saquear só a flecha (pote comum) esvaziava `items`; `_still_has_loot`
virava `False` mesmo com o Veneno de Aranha ainda intocado no pote
pessoal do jogador; o despawn genérico (`ENTITY_DESPAWN`, mesmo
mecanismo do bug de 17/07 e 25/07 documentados nesta seção antes)
disparava e removia a entidade da AOI de todo mundo — o item da quest
nunca mais ficava acessível.

**Fix**: `_still_has_loot` passa a checar também
`any(corpse_data.get("quest_rolls", {}).values())` — corpo só é
declarado "realmente vazio" se pote comum, moedas E o pote pessoal de
TODOS os jogadores estiverem vazios.

**Validado**: `tests/test_session.py::
TestHarvestableEmptyNaoDisparaDespawnGenerico` (2 testes novos) —
corpo com item pessoal pendente em `quest_rolls` não manda
`ENTITY_DESPAWN` ao saquear só o item do pote comum (reproduz o bug
relatado, reproduzido genuinamente via `git stash`); sem nada em
`quest_rolls`, o despawn continua disparando normalmente
(regressão, comportamento de 17/07 intacto). Suíte completa (600
testes) rodada 3x limpa — mesmas 2 falhas de sempre, sem relação
(§34.52).

**Validado em jogo pelo usuário**: "Validado." — confirmado antes da
suíte rodar (mesmo processo do §34.63/§34.64).

### §34.66 — Login/seleção de personagem ignoravam `window_mode` salvo
(28/07/2026)

Usuário relatou (2ª tentativa — já tinha reportado antes sem correção):
mesmo com o modo de janela configurado, a tela de login e a de seleção
de personagem sempre abriam em tela cheia.

**Investigação**: pedi confirmação antes de mexer, já que o menu só
tem um toggle "Tela cheia/Janela" (decisão de 24/07/2026) onde "Janela"
na verdade é `"windowed_fullsize"` (janela quase do tamanho do
monitor, sem maximizar de verdade) — descartei isso como causa depois
que o usuário esclareceu que o PRÓPRIO JOGO já respeita o modo
corretamente; só as 2 telas de pré-jogo (`main.py`) não.

**Causa raiz**: `main.py` nunca aplicava `window_mode` nenhum — os 2
pontos que chamam `pygame.display.set_mode()` (boot inicial e
reconexão após "Deslogar") sempre usavam um tamanho FIXO derivado só
de `scale` (`int(1280*scale)`, `int(720*scale)`), sem nenhuma flag de
tela cheia/redimensionável consciente do `window_mode` salvo. Com
`scale=1.5` (1920×1080 — resolução comum de monitor Full HD), a janela
cobria a tela inteira SEM nenhuma flag de fullscreen — visualmente
indistinguível de tela cheia de verdade — mesmo com `window_mode`
salvo como `"windowed_fullsize"`. Só o `GameEngine` (depois de entrar
no jogo) lê e aplica `window_mode` de verdade
(`_compute_and_set_window_mode`).

**Fix**: extraída a lógica de "calcular (width, height, flags) de
janela a partir do `window_mode`" pra uma função nova, `game.py::
compute_window_geometry(mode, scale)` — fonte única, chamada tanto por
`GameEngine._compute_and_set_window_mode()` (refatorado pra usá-la,
comportamento idêntico de antes, só sem duplicar o cálculo) quanto por
`main.py`, que agora lê `window_mode` do config (com a mesma migração
de `"maximized"` legado que `GameEngine` já fazia) e aplica a MESMA
geometria nos 2 pontos de `set_mode()`. `ui/login_screen.py`/`ui/
char_creation_screen.py` já liam `screen.get_size()` dinamicamente
(nenhuma mudança necessária ali) — só precisavam receber uma `Surface`
do tamanho certo.

**Validado**: `tests/test_client_ui.py` (3 testes) —
`compute_window_geometry("fullscreen", ...)` usa a resolução do
desktop + flag `FULLSCREEN`; `"windowed_fullsize"` usa desktop menos a
folga de 16×80px + `RESIZABLE` (sem `FULLSCREEN`); `"windowed"`
ignora completamente o tamanho do monitor (usa só `scale`) mesmo com o
desktop mockado bem maior — o núcleo da regressão. Confirmado via `git
stash` (`game.py`, `main.py`, testes fora do stash) que os 3 testes
falham genuinamente sem o fix (`ImportError` — a função não existia
antes). Suíte completa (603 testes) rodada 3x limpa — mesmas 2 falhas
de sempre, sem relação (§34.52).

**Validado em jogo pelo usuário**: "Testado e validado." — confirmado
antes da suíte rodar (mesmo processo do §34.63-§34.65).

### §34.67 — Clique num harvestable atravessava modal aberto (29/07/2026)

Usuário relatou: com algum modal aberto (ex: inventário), clicar em cima
de um harvestable no mundo fazia o personagem andar até ele, como se o
harvestable estivesse acima do modal na ordem de camadas. Também
perguntou diretamente se existe algum parâmetro configurável de
prioridade de camadas de UI, ou se é tudo hardcodado.

**Resposta à pergunta de arquitetura**: não existe um parâmetro único
configurável — a prioridade é convencional, espalhada em 3 lugares que
precisam ser mantidos em sincronia manualmente: (1) a ordem da cadeia
`elif` de despacho por evento em `game.py::run()`; (2) a lista de
prioridade declarada em `_modal_registry()`
(`client/modal_stack_handlers.py`, usada por `_topmost_open_modal()` —
mesma função que decide qual modal fechar com ESC); (3) exceções pontuais
por sistema que recebem eventos BRUTOS (não filtrados por
`_top_modal`) por motivo legítimo (ex: `LootSystem` precisa dos cliques
brutos pro seu PRÓPRIO modal de loot; a hotbar precisa de cliques em
coordenadas de tela). Nada impede estruturalmente que uma exceção nova
"esqueça" de checar `_top_modal` — é a mesma classe de bug do clique do
minimapa vazando (§34.59), agora reincidindo no `LootSystem`.

**Causa raiz**: em `game.py::run()`, o loop que decide se cada `system`
recebe `events` (brutos, sem filtro de modal) ou `systems_events`
(filtrados) tratava `LootSystem` como uma exceção incondicional:
`ev = events if system is self._loot_system else systems_events`. Com
QUALQUER modal aberto que não fosse o de loot (ex: inventário),
`LootSystem` ainda recebia o clique bruto, e sua lógica de "clicou num
harvestable no mundo → anda até ele" disparava por cima do modal.

**Fix**: `ev = events if (system is self._loot_system and _top_modal in
(None, "loot")) else systems_events` — `LootSystem` só recebe eventos
brutos quando NENHUM modal está aberto, ou quando o próprio modal de
loot é o que está aberto (preserva o clique legítimo dentro do modal de
loot).

**Validado**: 2 testes novos em `tests/test_client_ui.py` cobrindo
`_topmost_open_modal()` com/sem loot aberto — rotulados explicitamente
como testes de "ingrediente" (o comentário no teste documenta que eles
passam mesmo sem o fix de `game.py`, já que testam só o retorno de
`_topmost_open_modal()`, não o loop de despacho de eventos em si, que é
código inline não testável diretamente). Confirmado em jogo pelo
usuário.

### §34.68 — Clique na barra de ações desselecionava o alvo de combate
(29/07/2026)

Usuário relatou: atacando um alvo, clicar em algum botão da barra de
ações (hotbar) desselecionava o alvo e parava o ataque. Pedido:
interagir com a UI não deve interferir no combate.

**Causa raiz**: mesma classe de bug do §34.67 (evento vazando pra um
sistema que não deveria recebê-lo), só que na direção oposta — aqui o
problema não era `LootSystem` receber demais, era `MouseTargetingSystem`
receber um clique que JÁ tinha sido tratado pela hotbar.
`_handle_hotbar_click`/`_handle_consumable_bar_click` (`client/
hotbar_handlers.py`) eram chamados a partir de coordenadas de TELA (não
filtrados por `_top_modal`, correto — a hotbar não é um modal), mas não
sinalizavam de volta pra `game.py::run()` se o clique tinha de fato
acertado um slot. Sem esse sinal, quando NENHUM modal estava aberto o
mesmo clique de `MOUSEBUTTONDOWN` seguia adiante pra
`systems_events` e chegava também em `MouseTargetingSystem`, que
interpretava como "clique em chão vazio → desselecionar alvo".

**Fix**: `_handle_hotbar_click`/`_handle_consumable_bar_click` passam a
retornar `bool` — `True` sempre que o clique caiu em cima de um slot
(mesmo que a skill não tenha disparado de verdade por cooldown, talento
bloqueado ou shift+drag — o que importa é ter "consumido" o clique, não
se a ação teve efeito). `game.py::run()` declara `_hotbar_click_consumed
= False` antes do loop de eventos, captura o retorno das duas chamadas,
e adiciona `or _hotbar_click_consumed` à mesma cadeia de flags
(`_minimap_click_consumed`, `_right_click_consumed`, etc.) que já
suprime o `MOUSEBUTTONDOWN` de `systems_events` quando algum clique já
foi consumido no frame — mesmo padrão usado pra minimapa (§34.59).

**Validado**: 5 testes novos em `tests/test_client_ui.py` — clique fora
dos slots retorna `False` (hotbar e barra de consumíveis); clique em
cima de um slot aciona a skill/consumível E retorna `True`; clique em
slot com skill bloqueada por talento (`golpe_debilitante`, exige o
talento `cav_golpe_debilitante` alocado — testado com uma `TalentTree`
real com 0 pontos) retorna `True` sem disparar a skill; clique em
consumível durante cooldown global retorna `True` sem consumir de
verdade. Confirmado via `git stash` (`game.py`, `client/
hotbar_handlers.py` fora do stash, testes dentro) que os 5 falham
genuinamente sem o fix. Confirmado em jogo pelo usuário.

**Suíte completa (608 testes) 3x limpa pros §34.67/§34.68** — as
mesmas 2 falhas de sempre nesta sessão (`test_resolver_neutraliza_svc_
no_mapa_errado`, `test_disoriented_nao_bloqueia_movimento_bruto_no_
servidor`), confirmadas via `git stash` dos 3 arquivos de mapa
(`map_1_terrain.csv`/`map_1_objects.csv`/`map_1_entities.json`, em
edição concorrente pelo usuário) como causadas por terreno não-andável
em (131,374) perto do spawn de teste — sem relação com os fixes desta
sessão (ver §34.52).

### §34.69 — Sistema de nameplates: player remoto igual mob/NPC + ciclo
Shift+V de detalhe (29/07/2026)

Pedido do usuário, 4 partes: (1) nameplate de player remoto igual ao de
mob/NPC; (2) Shift+V desliga a visualização de nameplates de todo mundo
(player/mob/NPC), só o nome permanece; (3) Shift+V de novo mostra nome +
barra de vida "gerada pelo jogo" (sem a sprite PNG); (4) Shift+V de novo
volta ao completo (sprite/nameplate+nome, como é hoje).

**Decisões tomadas com o usuário antes de implementar** (perguntas
feitas via AskUserQuestion, mesma régua de `feedback_ask_before_
deciding`):
- Havia um asset novo, não integrado ainda, `assets/hud/
  namePlate_remotePlayer.png`/`.ase` (64×13, badge+barra estilo mob) —
  encontrado durante a investigação. Perguntado ao usuário se era pra
  usar esse asset: resposta foi **não**, reusar o `mob_hud_bar.png`
  existente (mesmo asset/função já usada por mob/NPC,
  `build_mob_hud()`). O asset novo continua no repo, não integrado —
  se for pra outra finalidade, tratar como pedido separado.
- "Barra de vida gerada pelo jogo" (item 3) = retângulo simples
  desenhado via `pygame.draw.rect` (cor por disposição, sem nenhum PNG
  de fundo/badge), SEM número de nível.
- O ciclo do Shift+V afeta TODO nameplate acima de sprite, inclusive o
  do PRÓPRIO personagem (não só terceiros).

**Item 1 — player remoto usa estilo de mob/NPC**: `client/
remote_entity_handlers.py::_draw_remote_players` trocou `build_player_
hud` (asset `player_hud_bar.png`, com as linhas de XP/recurso sempre
vazias pro player remoto — ele não expõe esse dado) por `build_mob_hud`
(asset `mob_hud_bar.png`, só badge+barra de HP — mesma função já usada
por mob local/remoto e badge de NPC). `build_player_hud` fica reservado
só pro HUD do PRÓPRIO player (`ui/systems.py::RenderSystem.render`, que
tem XP/recurso de verdade pra mostrar).

**Itens 2-4 — ciclo Shift+V (3 estados)**: novo campo `GameEngine.
_nameplate_mode: int` (`game.py`, default `0`, NÃO persistido em
config.json — reseta a cada sessão nova, mesmo espírito de outros
toggles de visualização como `_show_perf_overlay`). Tecla nova no loop
de eventos (`game.py`, ao lado do F10/F11): `pygame.K_v` + `KMOD_SHIFT`
→ `self._nameplate_mode = (self._nameplate_mode + 1) % 3`.

- **Modo 0** (completo, padrão): comportamento de sempre — badge/barra
  PNG + nome + ícones de efeito ativo.
- **Modo 1** (só nome): nenhum ícone/badge/barra/efeito é enfileirado
  em `WORLD_LABELS` — só o texto do nome.
- **Modo 2** (nome + barra simples): novo `ui/hud_bars.py::
  build_simple_hp_bar(hp_ratio, color)` — `pygame.Surface` pura via
  `pygame.draw.rect` (fundo escuro + preenchimento proporcional ao HP +
  borda preta), sem nenhum asset PNG, sem badge/número de nível, sem
  fila de efeitos.

Gate aplicado em 3 lugares (mesmo campo `self._nameplate_mode`, lido
direto por já ser mixin/parâmetro — nenhum estado duplicado):
- `ui/systems.py::RenderSystem.render(..., nameplate_mode=...)` — HUD
  do próprio player, badge de mob local/offline, badge de NPC (NPC sem
  `CombatStats` colapsa modos 1 e 2 no mesmo resultado — não tem HP pra
  desenhar barra nenhuma).
- `client/remote_entity_handlers.py::_draw_mob_hp_bars` — mob remoto
  (cor de disposição hostil/neutro/amigável preservada na barra simples
  do modo 2).
- `client/remote_entity_handlers.py::_draw_remote_players` — player
  remoto (idem, cor de hostilidade PvP preservada). Morto continua
  colapsando pra só-nome em QUALQUER modo (regra já existente,
  inalterada).

Em todos os 3 lugares, a fila de ícones de efeito de status (stun/
sleep/etc) só é montada no modo 0 — nos modos 1/2 o usuário pediu
explicitamente "a única coisa que permanecerá será o nome" / "somente o
nome e a barra de vida", exclusivo por definição.

**Validado**: 5 testes novos em `tests/test_client_ui.py` —
`RenderSystem.render()` com `nameplate_mode=0/1/2` (conta quantos
elementos vão pra `WORLD_LABELS._pending` por entidade: 2 no modo 0/2,
1 no modo 1; modo 2 confirma que a Surface do ícone NÃO bate com o
tamanho nativo de nenhum asset PNG, `P_SIZE`/`M_SIZE` × `SCALE`) +
2 testes equivalentes pra `_draw_remote_players`. Confirmado via `git
stash` (`game.py`, `ui/systems.py`, `ui/hud_bars.py`, `client/
remote_entity_handlers.py` fora do stash, testes dentro) que os 5
falham genuinamente sem o fix. Suíte completa (613 testes) 3x limpa —
as mesmas 2 falhas de sempre nesta sessão, sem relação (terreno em
edição do usuário, ver §34.52/§34.68).

**Validado em jogo pelo usuário**: "1. Validado; 2. Validado. Testei, e
ficou exatamente como eu queria."

### §34.70 — Sistema de Torres: estrutura estática com facção, ataque à
distância, alvo sticky com fidelidade ao LoL, respawn exato e XP/ouro
próprios (29/07/2026)

Pedido do usuário: nova entidade — **torre** — estática, com facção/
time, dano à distância a quem entra no alcance, recebe dano de volta,
usável tanto no mundo aberto quanto num campo de batalha estilo MOBA
(Arena 2x2 já existente reaproveitada pra isso, `Faction("arena_time_a"/
"arena_time_b")`). Parâmetros: respawnável + tempo de respawn, regenera
vida ou não, tipo de ataque (mágico/flecha), XP e ouro ao morrer.
Pesquisei mecânica de torre em League of Legends antes de implementar
(fonte real de "torre com facção que ataca quem entra no alcance") —
plano completo discutido e aprovado com o usuário antes de codificar.

**Modelo de dados** — `content/tower_definitions.py` (NOVO,
`TOWER_TABLE`): tabela própria, SEPARADA de `MOB_TABLE` (decisão do
usuário — torre não é um "mob"), mesmo formato de `attributes`
(health/armor/attack_min-max/attack_power/attack_speed/acerto/
crit_chance). 2 tipos de exemplo: `torre_de_fogo` (entity_class="Mago")
e `torre_de_flechas` (entity_class="Arqueiro") — sabor de projétil
reaproveita `PROJECTILE_BY_CLASS` (mob_definitions.py) de graça.

**Componente `Tower`** (`engine/components.py`) + **`create_tower()`**
(`engine/entity_factory.py`, construção MANUAL como
`create_training_dummy` — SEM `AIControlled`/`EnemyAISystem` de
propósito, a state machine de mob não serve pra algo 100% imóvel e
criava risco de bug). Campos: `tower_key`, `attack_range_tiles`,
`respawnable`, `respawn_s`, `regen_enabled`, `xp_reward`, `gold_min/
max`, `spawn_tile_x/y` (respawn EXATO), e runtime: `current_target_eid`
(sticky), `dmg_ramp_stacks`/`dmg_ramp_timer` (ramp vs player), `attack_cd`.

**`TowerSystem`** (NOVO — `engine/world_systems.py`) — targeting com
fidelidade real ao LoL (pesquisado e confirmado com o usuário):
1. **Prioridade absoluta**: mob/NPC hostil mais próximo no alcance
   SEMPRE antes de player (nunca escolhe player enquanto houver mob).
2. **Alvo sticky**: fixa no alvo até morrer/sair do alcance/perder LOS
   — NUNCA reavalia "o mais próximo" a cada tick (evita flicker).
3. **Aggro-switch**: player inimigo que dana um player ALIADO da torre
   dentro do alcance vira alvo IMEDIATO (override do sticky) — varre
   `_combat_this_tick` (entries `source in ("auto","skill")`) procurando
   esse padrão. Sem NPC no alcance, mira o player inimigo mais próximo.
4. **Ramp de dano**: confirmado na pesquisa que a mecânica real do LoL
   só vale contra CAMPEÃO — +40%/acerto até +120% (3 estocadas), NUNCA
   contra mob/NPC, reseta 3s sem bater em player, contador é DA TORRE
   (sobrevive a troca de alvo). Aplicado via novo campo
   `Projectile.dmg_multiplier` (default 1.0), lido por
   `ProjectileSystem.update()` e repassado a `deal_damage(multiplier=)`.

Sistema NÃO registrado em nenhum `self.systems`/`_systems` por-mapa
(mesmo princípio de `_tick_harvestable_respawn` — sweep global, filtra
`MapLocation` internamente) — `WorldServer` instancia 1x e chama
`update(dt, combat_this_tick=...)` manualmente a cada tick.

**Respawn exato** — NUNCA via `SpawnZone` (confirmado: `_pick_tile`
sempre sorteia tile aleatório no raio, errado pra estrutura fixa). Novo
`WorldServer._tower_respawn_timers` (chave `(map_file, tile_x, tile_y)`)
+ `register_tower_respawn()` (chamado por `ServerDeathHandler` antes de
remover a entidade, captura `Faction`/`EntityIdentity.level`) +
`_tick_tower_respawns()` — recria via `create_tower()` no MESMO tile.

**XP/ouro próprios** — `server/server_death_handler.py` ganhou um
desvio ANTES do lookup por nome/tier: se a entidade tem componente
`Tower`, usa `xp_reward`/`gold_min-max` direto, pula `MOB_TABLE`/
`roll_mob_coins` inteiramente (torre nunca cadastrada lá, de propósito).

**Dados de mapa** — `"towers"` (NOVO array em `{mapa}_entities.json`,
`engine/map_loader.py`), consumido por `WorldServer._create_towers()`.
2 torres de teste em `map_1_entities.json` (perto do spawn) + 1 torre
por time em `maps/arena_poco_negro_entities.json` (NOVO arquivo, torre
`arena_time_a`/`arena_time_b` — o mesmo mecanismo de facção que a Arena
2x2 já atribui aos players automaticamente ao aceitar a fila cobre a
torre sem nenhum código novo) + 1 NPC de combate "Minion"
(`faction="monstros_hostis"`) pra testar o aggro-switch com um alvo
sticky de mob real disponível.

**Cliente — zero protocolo novo**: torre sincroniza pelo MESMO pipeline
genérico de mob (`Combatant`+`TileMovement` → `_mob_eids` →
`ENTITY_SPAWN`) e o projétil pelo sweep genérico já existente de
`Position+Projectile` (`kind="mob_projectile"`, `server/world_server.py`
~linha 3944) — nenhuma mensagem nova.

**Testado**: `tests/test_towers.py` (NOVO, 14 testes) — prioridade,
sticky, aggro-switch, ramp (sobe/cap/reset), regen, respawn exato,
XP/ouro próprios, sync como "enemy". Corrigido efeito colateral real:
`tests/helpers.py::first_mob()` não excluía `Tower` — testes não
relacionados a torre passaram a pegar torre em vez de mob de verdade
(torre entra em `_mob_eids` pelo mesmo gate `Combatant`, sem
`AIControlled`/`NPC`) — corrigido junto.

**Validado em jogo pelo usuário**: torres hostis e amigáveis, respawn,
regen, XP/ouro, targeting mob>player, aggro-switch (com Minion de
teste), ramp de dano (números exatos 42→54→66→66..., batendo com a
fórmula), arena com times reais.

### §34.70.1 — 6 bugs reais achados no primeiro playtest completo da
Fase de Torres (29/07/2026)

**1. Ramp "não funcionava"**: falso alarme — o dano real subia
corretamente (confirmado por debug log dedicado, `TowerSystem._attack`
→ `debug/mob_combat_debug.py` MCL, ativável via `RPG_DEBUG_MOB_COMBAT=1`)
e o usuário depois confirmou com números exatos em teste controlado
(42, 54, 66, 66, 66, 66 — bate com `30_base × {1.4, 1.8, 2.2, 2.2...}`).
O que o usuário via antes era só a fase já estabilizada no cap (3
estocadas), sem ter capturado a subida inicial.

**2. Causa raiz REAL do som (chave de destravamento pra tudo abaixo)**:
o CLIENTE reconstrói qualquer entidade remota "enemy" via
`create_enemy()` → `MOB_TABLE.get(race)`. Como torre tem tabela PRÓPRIA
(`TOWER_TABLE`, decisão do usuário), o cliente NUNCA achava a definição
— caía no template genérico 100% errado: `entity_class` virava sempre
"Arqueiro"/"Guerreiro" (nunca "Mago" de verdade, mesmo pra torre de
fogo) e `NpcSounds` ficava TOTALMENTE vazio (`mob_def=None`). Fix:
`engine/entity_factory.py::_build_combat_entity` — lookup agora é
`MOB_TABLE.get(race) or TOWER_TABLE.get(race)` (`TOWER_TABLE` ganhou
`color`/`is_ranged`/`move_speed_pct` só pra satisfazer os acessos
obrigatórios dessa função). Isso TAMBÉM corrigia sozinho o visual do
projétil (ver #3) — os dois bugs eram o MESMO bug.

**3. "Círculo laranja" em vez do sprite da bola de fogo**: eu tinha
investigado errado e afirmado que não existia visual melhor no jogo —
o usuário testou com a Selene Vail e provou que existia
(`client/remote_entity_handlers.py::_spawn_mob_projectile`, sweep
genérico de `Position+Projectile` → cria um `PlayerProjectile` local
com `spell_id="bola_de_fogo"` se `entity_class` do atacante for
Mago/Mage). Resolvido pelo MESMO fix do item #2 (`entity_class`
correto → `_spawn_mob_projectile` já escolhe o visual certo sozinho).

**4. Nomes de som fictícios**: `TOWER_TABLE` original tinha
`"fireball_cast"`/`"fireball_impact"`/`"bow_shot"`/`"tower_destroyed"`
— nomes inventados sem checar `assets/sounds/sfx/`, nenhum existia de
verdade (por isso "nenhum som" na magia). Trocados pelos MESMOS
arquivos reais já usados por "Mago (NPC)"/"Arqueiro (NPC)"
(`skill_bola_de_fogo_launch/_impact`, `arrow_release`/`arrow_impact`).

**5. Log de combate/som atribuindo a torre errado**: `_mob_attacker_of`
(`server/combat_processor.py`, reverse-map "quem atacou este player")
só reconhecia atacante com `AIControlled` — torre nunca aparecia,
resolvendo `attacker=-1` ou (pior) herdando do cache
`_last_mob_attacker` o atacante ERRADO de um mob real anterior. Fix:
mesmo loop agora também registra `Tower.current_target_eid`. Também
corrigido: `is_ranged` sempre `False` no `ENTITY_SPAWN` da torre
(`_build_mob_spawn_payload`, `server/world_server.py` — sem
`AIControlled`, nunca setava `True`).

**6. Som de lançamento tocando no momento do IMPACTO**: bug crônico já
visto antes nesta sessão (nameplate/hotbar), agora numa 3ª forma —
`_play_attacker_mob_sound` (`client/remote_entity_handlers.py`, toca na
CHEGADA do golpe no player) usava as chaves de LANÇAMENTO
("attack_ranged"/"attack_magic") em vez de "attack_impact", porque
checava `AIControlled` do espelho remoto — que NUNCA existe (removido
de propósito em `_spawn_remote_mob`, servidor é autoritativo pra IA).
Trocado pra `EntityIdentity.entity_class` (mesmo padrão que a função
irmã `_play_nonplayer_attack_impact`, mob-vs-mob, já fazia certo) +
prioriza `attack_impact` quando configurado. Consequência em cascata: o
som de LANÇAMENTO de verdade também nunca tocava mirando o player local
(`_spawn_mob_projectile` pulava de propósito, assumindo — errado — que
o impacto já cobria isso) — agora toca sempre, sem duplicar (launch e
impact usam chaves DIFERENTES). Afeta qualquer mob/NPC ranged ou
mágico atacando o player, não só torre.

**7. Aggro-switch sem candidato pra testar na arena**: `arena_time_a`/
`arena_time_b` nunca tinham relação declarada com nenhuma facção de
mob (`content/faction_data.py`), caindo em "neutro" por padrão —
`TowerSystem` só aceita `is_hostile()` (exige tier "hostil"), então a
torre de arena nunca via NENHUM mob como candidato válido. Adicionado
`("arena_time_a"/"arena_time_b", "monstros_hostis"): "hostil"` (minion
neutro, hostil aos dois times por igual — mesmo princípio de minion do
LoL) + NPC de combate "Minion" de teste na arena.

**Validado em jogo pelo usuário**: som (lançamento+impacto, torre de
fogo e de flechas), visual (sprite da bola de fogo), aggro-switch com
Minion real na arena — "funcionou perfeitamente".

Suíte completa (636 testes) 3x limpa — as mesmas 2 falhas de sempre
nesta sessão, sem relação (terreno em edição do usuário, ver §34.52).
Vários testes novos por fix, todos confirmados via `git stash` que
falham genuinamente sem a correção correspondente.

### §34.71 — Zoom padrão vira 200% + remove indicador fixo de zoom (29/07/2026)

Pedido do usuário: zoom abrir em 200% (em vez de 150%, o mínimo
permitido) e tirar o texto "zoom X%" do canto superior esquerdo da
tela.

`GameEngine.__init__` ganhou `self._zoom_default: float = 2.0` (dentro
do range já existente `_zoom_min=1.5`/`_zoom_max=2.5`) — os 2 pontos
que antes inicializavam com `self._zoom_min` (`self._zoom` em
`_load_map_and_entities`/pós-spawn, e `_zoom_pending` no `__init__`)
passam a usar `_zoom_default`. `_zoom_min`/`_zoom_max` continuam
intactos como limites do scroll/+−, nenhuma mudança de comportamento
aí.

Indicador removido (`client/hud_handlers.py::_draw_hud`): a condição
`if self._zoom != 1.0` nunca foi falsa na prática, já que o zoom mínimo
permitido é 1.5 — o texto aparecia sempre, não era um indicador
condicional de verdade.

Mudança pequena e de baixo risco (constante + remoção de bloco morto,
sem lógica nova) — sem teste dedicado (nenhuma infraestrutura de teste
pra `GameEngine`/`_draw_hud` existia pra reaproveitar, e não há
comportamento condicional restante pra cobrir). Validado visualmente
pelo usuário em jogo antes da suíte rodar. Suíte completa (636 testes)
3x limpa.

### §34.72 — Visão compartilhada de time (instanciado) (30/07/2026)

Pedido do usuário: dar "time" aos NPCs (pra campo de batalha estilo
MOBA) e compartilhar visão entre membros de um time — players, torres e
minions (NPCs) — nesse campo de batalha, generalizando pra arena (já
existe) e futuramente battlefield/dungeon. Decisões confirmadas com o
usuário via `AskUserQuestion`:

- **"Time" = `Faction` reaproveitada exatamente como já existe** — sem
  conceito novo de `team_id`. "Times pros NPCs" não exigiu NENHUM código
  novo: dar time a um NPC/minion já era só anexar
  `Faction(faction_id="arena_time_a")` nele (mesmo padrão que a torre já
  usa, §34.70). O trabalho real desta feature foi a visão compartilhada.
- **Escopo: SÓ conteúdo instanciado** (arena hoje; battlefield/dungeon
  futuro) — NUNCA grupo/party de mundo aberto. Gate: `Faction` EXPLÍCITA
  no player (`world.get_component` direto, nunca
  `get_entity_faction`/resolver — que tem fallback pro default de mundo
  aberto e vazaria visão pra todo mundo). Players de mundo aberto normais
  nunca têm `Faction`.
- **Raio de contribuição de visão por tipo de entidade** (pedido
  explícito do usuário, `shared/constants.py`):
  `ALLY_VISION_RADIUS_PLAYER=15` (= `AOI_RADIUS`),
  `ALLY_VISION_RADIUS_TOWER=18`, `ALLY_VISION_RADIUS_MINION=8`.
- **Chat de proximidade também viaja pela visão de time** (pedido
  explícito) — `_sessions_in_aoi` (usado por chat, skill, som, broadcast
  direto) respeita visão de time sempre, sem parâmetro de exceção.

**Mecanismo** (`server/session.py::SessionManager`):

1. `_compute_ally_vision_centers()` roda 1x/tick, no topo de
   `_dispatch_tick_deltas` (ANTES de qualquer AOI), e guarda em
   `self._ally_vision_centers: dict[player_eid, list[(tx,ty,radius)]]`.
   Agrupa players com `Faction` explícita em buckets
   `(map_file, faction_id)` (via `WorldServer.get_player_map` — NÃO
   `_player_match_id`, que é bookkeeping específico da arena e não
   generaliza pro battlefield/dungeon futuro); se nenhum player tiver
   Faction, retorna `{}` sem tocar em `_mob_eids` (custo ~zero fora de
   contexto de time). Depois faz UMA passada em `_mob_eids` — só mobs
   cujo `(map_file, faction_id)` já bate um bucket existente entram,
   com raio de torre ou minion conforme tenham o componente `Tower`.
   Cada membro do bucket vira centro extra pros outros (nunca pra si
   mesmo).
2. `_build_update_for_session` ganha `ally_centers` opcional — os
   closures `in_aoi`/`in_aoi_exit` (antes single-center) passam a
   checar "qualquer centro" (posição própria + centros de aliados,
   cada um com seu próprio raio). O pré-filtro do `SpatialHash`
   (`mob_hash.nearby`) vira união dos candidatos de CADA centro (usando
   o maior raio possível na query) antes do check exato por centro.
3. `_sessions_in_aoi` ganha a MESMA generalização, mas 100% internamente
   — nenhum dos ~12 call-sites (skill/som/chat/corpse) muda de
   assinatura; o loop só passa a montar `[(sx,sy,AOI_RADIUS)] + aliados`
   por sessão candidata.

`_can_see()` (gate de invisibilidade/Camuflagem) continua checado POR
ENTIDADE no ponto de inclusão — união de centros não bypassa
invisibilidade, nenhuma mudança precisou entrar ali. `known_eids` não
precisou de mudança de schema: os centros são recalculados do ZERO todo
tick a partir do estado atual (Faction+posição), então "aliado A sai do
range mas aliado B ainda cobre" resolve sozinho sem cache por origem.

**Gap pré-existente encontrado (não desta feature, fora de escopo
consertar agora)**: o bloco `deltas["spawned"]` em
`_build_update_for_session` nunca chamou `_can_see` (só `in_aoi`) — um
player recém-logado entra em `known_eids` de quem já está por perto NO
MESMO tick do login, camuflado ou não. Só importa numa janela estreita
(entidade nova aparecendo já invisível no mesmo tick que alguém a
descobre) — documentado em `tests/test_session.py::
TestAllyVisionSharing.test_camuflagem_ainda_bloqueia_mesmo_visivel_via_aliado`
(o teste evita a janela pra validar a propriedade real desta feature).

Testes: `tests/test_session.py::TestAllyVisionSharing` (10 testes) —
mundo aberto sem Faction custa zero, teammate estende visão além do
próprio AOI, torre/minion contribuem raio 18/8 (não 15), times
inimigos/instâncias diferentes sem visão cruzada, `_sessions_in_aoi`
inclui via visão de time, Camuflagem ainda bloqueia, `known_eids` não
oscila quando um aliado sai mas outro cobre, Faction removida some no
tick seguinte. Verificado com `git stash` (8/10 falham genuinamente sem
a implementação — os 2 restantes são só-negativos, que também "passam"
sem a feature já que a ausência da feature IMPLICA a ausência do vazamento
que eles checam).

#### §34.72.1 — Correção de escopo: névoa (fog-of-war/minimap) também precisa ser compartilhada (30/07/2026, mesmo dia)

Validação em jogo do usuário revelou que a entrega acima (só sync de
ENTIDADES via AOI) não bastava: "eu quero que as torres e minions do
time explorem a fog, ou seja, da mesma forma que eu vejo a minha. É
idêntico ao LoL" — ward/torre/minion também precisa acender a névoa do
MINIMAPA (`FogOfWar.explored`) e o LOS de renderização
(`FogOfWar.visible`), não só entregar as entidades brutas por AOI. Erro
de escopo meu: o campo de visão (`FogOfWar`/`FogSystem`,
`ui/systems.py`) é 100% CLIENT-side e nunca fez parte do desenho inicial
— só considerei o sync de entidades (servidor→cliente), não a
exploração de mapa (puramente visual, client-side).

**Mecanismo**: `SessionManager._ally_vision_centers[player_eid]`
(já calculado pra AOI) é enviado ao PRÓPRIO dono, dentro do
`AOI_UPDATE`, como `"ally_vision_centers": [[tx,ty,radius], ...]` — só
quando não-vazio (mesmo princípio de custo zero fora de contexto de
time; servidor decide QUEM/RAIO, cliente só desenha, igual todo o resto
do projeto). Cliente (`client/network_handlers.py::
_handle_msg_aoi_update`) grava isso em `FogOfWar.ally_centers` (campo
novo). `FogSystem.update()` (`ui/systems.py`) passa a:
- Recomputar quando o player muda de tile **OU** quando
  `fog.ally_centers` muda (torre parada não muda nunca; minion/teammate
  se movendo, sim) — antes só recomputava no movimento do PRÓPRIO
  player.
- Rodar `compute_fov(ax, ay, ar, is_blocking)` (mesmo shadowcasting já
  usado pro player, `ui/fov.py`) a partir da posição de CADA aliado
  (não da do player) e unir o resultado em `fog.visible` E
  `fog.explored` — é o shadowcast PRÓPRIO do aliado que revela área que
  o player não enxergaria em linha reta (ex: atrás de um pilar/parede
  que bloqueia LOS a partir da posição do player, mas não da torre).
- `FogOfWar.switch_map()` descarta `ally_centers` do mapa anterior
  (coordenadas de outro mapa não fazem sentido no novo — resolve
  sozinho na próxima AOI_UPDATE do mapa novo).

**Simplificação aceita conscientemente**: se o servidor OMITE o campo
(ally_centers ficou vazio), o cliente NÃO recebe um "clear" explícito —
`fog.ally_centers` fica com o último valor conhecido até a próxima
mudança de mapa (`switch_map`, que sempre acontece ao entrar/sair de
arena/instância). Staleness prática é ~zero: dentro da MESMA partida os
aliados não "somem" de verdade enquanto você permanece; ao fim da
partida todo mundo é transferido de mapa, o que já limpa tudo. Não
vale a complexidade de rastrear "mudou de vazio pra vazio" só pra esse
caso.

Testes: `tests/test_fog_ally_vision.py` (6 testes) — servidor inclui/
omite `ally_vision_centers` conforme haja aliado; torre aliada do outro
lado de uma parede revela tile que o player não veria sozinho (`visible`
E `explored`); sem aliado, parede bloqueia normalmente (regressão);
recomputa quando só o aliado se move (player parado); `switch_map`
descarta centros do mapa anterior. Verificado com `git stash` (5/6
falham genuinamente sem a implementação — o 6º é só-negativo/regressão).

#### §34.72.2 — Raio de visão da torre vira dado por TIPO, não constante global (30/07/2026, mesmo dia)

Usuário foi testar em jogo (ajustar o raio pra validar) e não achou
nenhum parâmetro em `content/tower_definitions.py` — o raio
(`ALLY_VISION_RADIUS_TOWER=18`) tinha ficado como constante GLOBAL única
em `shared/constants.py`, aplicada igual pra QUALQUER torre,
inconsistente com o padrão já usado por `attack_range_tiles` (esse sim
por tipo, na tabela). Corrigido: `TOWER_TABLE[tipo]["vision_radius_tiles"]`
(novo campo, `content/tower_definitions.py`) → `Tower.vision_radius_tiles`
(novo campo no componente, gravado por `create_tower` na criação,
default = `ALLY_VISION_RADIUS_TOWER` se a definição omitir) →
`_compute_ally_vision_centers` agora lê `Tower.vision_radius_tiles` do
componente, nunca mais a constante direto (a constante só sobra como
DEFAULT de criação). `ALLY_VISION_RADIUS_MINION` continua constante
global única (minion/NPC não tem tabela por-tipo equivalente hoje) —
mesma assimetria sinalizada ao usuário, não mudada sem confirmação.

Teste: `tests/test_session.py::TestAllyVisionSharing::
test_raio_de_visao_da_torre_vem_do_dado_por_tipo_nao_de_constante_global`
— cria torre com override pontual de `vision_radius_tiles=5` e confirma
que um mob a 10 tiles (dentro do default 18, fora do override 5) NÃO
aparece. Verificado com edição temporária (não `git stash`, já que todo
o trabalho desta sessão ainda está uncommitted — stashar o arquivo
inteiro reverteria a feature toda, não só este refinamento) revertendo
só a linha do cálculo de raio pra ler a constante direto: o teste falha
genuinamente nesse cenário (mob aparece), confirmando que o dado da
tabela é o que realmente importa agora.

#### §34.72.3 — `ARENA_GATE_TILES` desatualizado após o usuário aumentar o mapa da arena (30/07/2026, mesmo dia)

Usuário aumentou `maps/arena_poco_negro.csv` (mapa maior) e ajustou os
spawns de `ARENA_MODES["spawns_a"/"spawns_b"]` (`server/match_processor.py`)
pra nascer dentro do portão novo, mas `ARENA_GATE_TILES`
(`shared/constants.py`) — fonte única compartilhada servidor/cliente pra
saber QUAIS tiles são o portão físico (§34.34-ish, revisão 22/07/2026) —
continuava com as coordenadas do mapa ANTIGO: `(12-14, 4)`/`(12-14, 30)`.
No mapa novo, essas células são parede comum (`#`) permanente — nunca
fazem parte do mecanismo de abrir/fechar — e o portão de VERDADE (tile
`D`, achado varrendo o CSV) está em `(17-29, 4)`/`(17-29, 41)` (13 tiles
de largura cada lado, não mais 3). Bug real, não só de teste: o portão
nunca teria aberto/fechado direito em produção com a constante velha —
achado ao investigar `tests/test_arena.py::TestArenaAceiteContagem::
test_fim_do_preparo_abre_portao_fisico` falhando após o resize do mapa.
Corrigido atualizando `ARENA_GATE_TILES` pras 26 coordenadas reais.

### §34.73 — Sistema de Minions: lane creeps estilo MOBA (30/07/2026)

Pedido do usuário: NPCs que nascem na base do time em waves (3 melee +
3 à distância + 1 à distância tier raro), andam por um caminho até a
base inimiga com checkpoints a cada 5 tiles, enfrentam qualquer hostil
(torre/NPC/player) que entrar no raio de aggro, e ao perder o alvo
voltam pro ÚLTIMO CHECKPOINT (não pra base) antes de retomar o caminho.
XP compartilhado; moeda deixada pra outro momento (pedido explícito).

Decisões confirmadas com o usuário via `AskUserQuestion` (não perguntar
de novo):

- **Sistema de IA PRÓPRIO** (`MinionSystem`, `engine/world_systems.py`)
  — sem `AIControlled`/`EnemyAISystem` compartilhado, que só sabe voltar
  pro spawn fixo e ficar parado pra sempre depois (não suporta
  "checkpoint + retomar avanço"). Mesma decisão já tomada pra Torre
  (§34.70) — zero risco de regressão nos mobs/NPCs do mundo aberto.
- **XP flui pelo mesmo pipeline que Torre já usa** (hook em
  `server/server_death_handler.py`, ANTES do fallback de `MOB_TABLE`) —
  mas com tabela PRÓPRIA (`content/minion_definitions.py::MINION_TABLE`),
  já que a construção da entidade também é própria (não passa por
  `_build_combat_entity`).
- **Split de XP** reaproveita o mecanismo de GRUPO que já existe
  (`PARTY_XP_SHARE_RADIUS_TILES=15`, `server/party_processor.py::
  _party_members_in_range`) — times de arena (2v2/3v3) já são grupo de
  verdade (fila exige estar em grupo do tamanho do time), então morte
  de minion cair no mesmo `pending_xp`/pipeline de Torre/mob já
  compartilha automaticamente, ZERO código novo de split.
- **Intervalo de wave CONFIGURÁVEL por lane** (campo `wave_interval_s`
  no JSON do mapa, mesmo padrão de `respawn_s` da Torre — não constante
  global), default 45s.
- **Moeda/gold fora de escopo** — `coins=0, loot_items=[]` fixo (mesmo
  padrão de Torre quando não configurada).

**Modelo de dados** — `content/minion_definitions.py::MINION_TABLE`:
3 tipos (`minion_melee`/`minion_ranged`/`minion_ranged_raro`, esse
último com `tier="rare"` — já existe em `ENEMY_TIER_CONFIGS`, hp×3/
dmg×2/xp×4). Campo NOVO em relação a `TOWER_TABLE`:
`aggro_range_tiles` (raio de PERCEPÇÃO, separado de
`attack_range_tiles`/alcance de ataque — Torre usa o mesmo valor pros
dois papéis, minion precisa perceber de mais longe do que ataca,
mesma distinção que `AGGRO_RADIUS_TILES` vs `attack_range_tiles` já
fazem no `EnemyAISystem`, só que por TIPO em vez de constante global —
mesma filosofia da correção §34.72.2).

**Componente `Minion`** (`engine/components.py`): `route` (path
COMPLETO base→base, calculado 1x via pathfinding no spawn da wave) +
`route_idx` (ÚNICA fonte de verdade de progresso — "checkpoint" é
DERIVADO, `route_idx // 5 * 5`, não uma lista separada autorada à
mão — nenhum conceito de waypoint/checkpoint existia no codebase antes
disso) + `current_path`/`path_recalc_timer` (caminho tile-a-tile CURTO,
recalculado via A* real sempre que o minion precisa andar até um
destino que pode estar a VÁRIOS tiles — perseguir alvo, voltar pro
checkpoint, ou retomar `route` depois de ter sido puxado pra fora dela
em combate — sem isso, `start_tile_movement` trataria qualquer
distância como 1 tile só, virando um "salto" instantâneo pra destinos
distantes; bug real pego em teste manual ANTES do commit, corrigido
adicionando repathing tipo `AIControlled.path`/`EnemyAISystem`).

**`MinionSystem.update()`** por minion a cada tick: máquina de estado
própria `ADVANCING`/`FIGHTING`/`RETURNING`. Aggro/alvo usa `is_hostile`
(mesmo resolver de Faction que Torre já usa — zero dado novo, times
`arena_time_a`/`arena_time_b` já hostis entre si) SEM a nuance de
"mob > player" que Torre tem (não pedida aqui — pega só o hostil mais
próximo, seja torre/NPC/player). Ataque melee = `deal_damage` direto;
ranged = `Projectile` via `_spawn_attack_projectile` (helper NOVO,
extraído de `TowerSystem._attack` — MinionSystem virou o 2º consumidor
real da mesma lógica de spawn de projétil, duplicar pela 2ª vez deixou
de valer a pena).

**Wave spawning** (`WorldServer`): `_minion_lanes` (config por mapa,
registrada em `_create_minion_lanes` durante `_load_map_for`, mesmo
padrão de `_create_towers`) + `_minion_wave_timers` (chave presente =
lane ATIVA). DIFERENTE do respawn de Torre (`_tick_tower_respawns`, 1
morte→1 timer→1 respawn no MESMO tile): minion é timer POR LANE,
incondicional (dispara sozinho no relógio, sempre em LOTE de 7) — morto
NÃO agenda respawn individual, só a próxima wave programada cria
minions novos. **Trigger de ativação**: `_activate_minion_lanes`,
chamado por `server/match_processor.py::_tick_arena_pending` no EXATO
momento em que `fight_started=True` (portão físico abre) — nunca
durante o preparo.

**Dados de mapa** — `maps/{mapa}_entities.json::"minion_lanes"` (novo
array, `engine/map_loader.py`): `faction`/`spawn_tile`/`target_tile`/
`wave_interval_s`/`level` por lane.

**2 bugs da MESMA classe já corrigida pra Torre, re-encontrados e
corrigidos proativamente** (Torre não tem `AIControlled`, então
qualquer código que assume "mob = tem AIControlled" fica cego pra
Torre — e agora também pra Minion):
- `_build_mob_spawn_payload` (`server/world_server.py`) sempre mandava
  `is_ranged=False` pra minion (preso no default, já que não tem
  `AIControlled` nem é sempre-ranged como Torre) — cliente escolheria
  som/animação de melee pra minion ranged. Corrigido com branch
  `elif Minion: is_ranged = attack_range_tiles > 1`.
- `_mob_attacker_of` (`server/combat_processor.py`, reverse-map pra
  atribuição de combat log/som quando mob ataca player) só reconhecia
  `AIControlled`/`Tower` — dano de minion em player perdia o atacante
  certo (caía em `-1` ou herdava o `_last_mob_attacker` errado).
  Corrigido com o mesmo branch que Torre já tinha, agora pra `Minion.
  current_target_eid`.

Testes: `tests/test_minions.py` (15 testes) — avanço ao longo da rota,
engajamento de hostil no raio de aggro, dano melee direto, projétil
ranged com sabor de classe correto, nunca ataca aliado, volta pro
ÚLTIMO CHECKPOINT (não pro spawn) ao perder o alvo (sondagem tick-a-
tick, não contagem fixa — o tempo de ida-e-volta depende da distância
real), retoma avanço depois de voltar, composição exata da wave (3/3/1),
rota calculada via pathfinding real, intervalo configurável por lane,
lane inativa nunca spawna, XP flat da própria definição, sem ouro/loot,
`is_ranged` correto por tipo, sync via pipeline genérico com Faction
correta. Verificado com `git stash -u` (todos os 15 falham genuinamente
sem a implementação — erro de import na coleta).

#### §34.73.1 — 2 bugs reais de movimento achados no playtest do usuário (30/07/2026, mesmo dia)

**1) Lane da arena nunca foi criada de verdade**: implementei todo o
mecanismo (dado/sistema/wave/hook), mas esqueci de adicionar o array
`"minion_lanes"` no `maps/arena_poco_negro_entities.json` real — sem
isso, `_create_minion_lanes` nunca registrava nada e nenhuma wave
nascia. Corrigido com 2 lanes (`arena_time_a`/`arena_time_b`, coluna 23,
espelhadas, `wave_interval_s=45`). Validado com script de integração
completo (fila → aceite → força fim do preparo → roda ticks → confirma
14 minions com a composição certa).

**2) Minion "andava 1 tile, parava ~0,8s, repetia"**: bug real no
throttle de `MinionSystem._walk_toward` — o backoff de retry de
pathfind (`PATH_RECALC_INTERVAL=0.8s`, existe pra não martelar
`find_path` todo tick quando o MESMO destino falha repetido) estava
sendo aplicado também na PRIMEIRA tentativa de cada tile novo da rota
(`current_path` vazio por ter acabado de ser consumido com sucesso
parecia idêntico, pro código, a "vazio por ter falhado"). Corrigido
adicionando `Minion.path_dest` — só entra em backoff quando o destino
é o MESMO de uma tentativa anterior que falhou; destino novo tenta na
hora. Confirmado via trace tick-a-tick (script de diagnóstico): antes
do fix, ~16 ticks (0,8s) parado entre CADA tile; depois, 1 tick (o
natural, entre "chegou" e "emitiu o próximo passo"). Teste de
regressão: `test_minion_nao_faz_pausa_artificial_entre_tiles_da_rota`
(sonda ociosidade tick-a-tick, falha se qualquer intervalo > 2 ticks).

Nota técnica à parte (não um bug de produção, mas achado durante o
diagnóstico): rodar `tests/test_minions.py` depois de `tests/
test_towers.py` (que usa `WorldServer` real) travava a suíte inteira —
`engine.world_systems._svc_resolver` (global, setado por
`register_service_resolver` no `WorldServer.__init__`) ficava apontando
pro `WorldServer` antigo (mapa real 300x603), e um eid reaproveitado no
`World()` headless pequeno de `test_minions.py` resolvia o bundle
ERRADO em `is_tile_walkable`. Corrigido no teste (`_make_world_with_
services()` chama `register_service_resolver(None)` antes de montar o
World headless) — não é uma mudança de código de produção.

#### §34.73.2 — Refinamentos de playtest: lanes múltiplas, pileup de minion, level/XP dinâmicos (30/07/2026, mesmo dia)

Usuário validou a arena (com ressalva: vai validar melhor num mapa MOBA
de verdade) e trouxe 4 pontos:

1. **Múltiplas lanes por time (top/mid/bot)**: `"minion_lanes"` já
   aceitava várias entradas por mapa, mas `_minion_wave_timers` era
   chaveado só por `(map_file, faction)` — 2 lanes do MESMO time
   colidiam na mesma chave, e só a primeira jamais disparava. Corrigido
   com campo NOVO `lane_id` (JSON, default `"default"` — mapa com 1
   lane por time, como a arena hoje, não precisa declarar nada) e chave
   `(map_file, faction, lane_id)` em `_activate_minion_lanes`/
   `_tick_minion_waves`.
2. **Minions travando uns nos outros**: tinham pathfinding (A*) desde o
   início, mas eu nunca passava as posições ocupadas por OUTROS minions
   pro pathfinder (`dynamic_obstacles`) — cada um martelava repath
   contra um tile ocupado sem NUNCA desviar, mesma classe de bug que
   `EnemyAISystem` já resolve pros mobs comuns (`_get_occupied_tiles`).
   Adicionado `MinionSystem._get_occupied_tiles(map_file, except_eid)`
   (mesmo princípio, parametrizado por mapa) passado como
   `dynamic_obstacles` em `_walk_toward`. Detalhe importante: o próprio
   `dest` (tile-alvo do pathing — seja o alvo de combate ou o
   checkpoint) é excluído do bloqueio, porque A* rejeita um DESTINO
   ocupado inteiro (nem acha caminho parcial) — isso quebrava perseguir
   um alvo (o tile dele sempre "ocupado" por ele mesmo) e voltar pro
   checkpoint quando outro minion já estava nele. Chegada de verdade
   continua decidida por distância no chamador (attack_range_tiles/
   `CHECKPOINT_ARRIVAL_RADIUS`), não por pisar no tile exato — ver
   próximo item.
   - Complementar: **checkpoint virou ÁREA** (`CHECKPOINT_ARRIVAL_
     RADIUS=2` tiles, pedido do usuário — sugestão dele mesmo), não
     mais um tile único exato — vários minions voltando pro MESMO
     checkpoint disputavam o único tile exato e travavam uns nos
     outros (quem chegava primeiro ocupava, o resto nunca "pisava"
     nele). Chegar a até 2 tiles já conta como "voltou"; `route_idx`
     é ajustado pro índice do checkpoint ao "chegar" (mesmo sem estar
     exatamente nele) — sem descontinuidade ao retomar `ADVANCING`.
3. **Level do minion = média do time**: `WorldServer.
   _compute_team_avg_level(map_file, faction_id)` (NOVO) — mesma
   Faction EXPLÍCITA + `get_entity_map` já usados em visão de time
   (generaliza pra battlefield/dungeon futuro, não depende de
   `_player_match_id`/bookkeeping específico da arena). Recalculado a
   CADA wave (`_tick_minion_waves`), não fixado 1x — acompanha quem
   subiu de level durante a partida. Sem player do time na instância,
   cai no `level` estático da lane (fallback).
4. **XP escala com level**: já que o level virou dinâmico, `server_
   death_handler.py`'s hook de minion mudou de `base_xp = xp_reward`
   pra `base_xp = xp_reward × identity.level` (mesmo `EntityIdentity.
   level` gravado por `create_minion`, fonte única, nunca duplicado
   num campo próprio do componente `Minion`).

Testes novos: `tests/test_minions.py` — 2 lanes do mesmo time disparam
independente (`lane_id`), level = média exata do time (4+8)/2=6,
fallback estático sem player do time, XP = `xp_reward×level` exato.
Suíte completa 3x limpa (só as 2 falhas pré-existentes conhecidas).

#### §34.73.3 — Lane com curva: `target_tile` vira lista de waypoints (30/07/2026, mesmo dia)

Usuário percebeu que uma lane hoje só tinha 2 pontos (spawn/target) —
suficiente pra uma reta (lane "mid"), mas top/bot de um mapa MOBA de
verdade fazem curva, precisando de waypoint(s) intermediário(s) antes
da base inimiga. Proposta do usuário, confirmada: `target_tile` aceita
ou um par único `[x,y]` (formato de sempre, reta) OU uma lista de
waypoints `[[x,y],[x,y],...]` — o minion passa por cada um em ordem,
o ÚLTIMO da lista é a base de verdade.

Encaixou sem tocar em `Minion`/`MinionSystem`: `route` já era só uma
lista PLANA de tiles, então "chegou de 1 pathfind só" ou "de N pernas
emendadas" é indistinguível pra quem consome (route_idx/checkpoint
continuam idênticos). Só 2 pontos mudaram:
- `engine/map_loader.py`: normaliza os 2 formatos JSON aceitos pra
  SEMPRE virar lista de tuplas internamente (`target_tile` nunca mais
  é um par solto depois do parse — quem consome nunca precisa saber
  qual formato foi usado no JSON).
- `server/world_server.py::_tick_minion_waves`: em vez de 1
  `find_path(spawn, target)`, encadeia 1 `find_path` por PERNA
  (spawn→wp1→wp2→...→última) e concatena tudo num `route` só. Se
  QUALQUER perna falhar (bloqueada/inalcançável), o minion inteiro
  daquela wave é pulado (mesmo comportamento de sempre pra falha de
  pathfind) — não spawna com rota parcial.

Ajuste proativo junto: `manhattan_limit=None` explícito nas pernas de
lane (o default de `find_path`, 60 tiles, rejeitaria silenciosamente
uma perna longa num mapa MOBA de verdade — limitação latente mesmo
antes desta mudança, com 1 perna só).

Testes novos: `tests/test_minions.py` — lane com 2 waypoints produz
rota que passa pelo intermediário ANTES do final (índice comparado);
`engine.map_loader._merge_entities_json` normaliza os 2 formatos
(par único → lista de 1; lista de waypoints → preservada, como
tuplas) — testado direto contra o parser real, não só o helper de
teste. Suíte completa 3x limpa (675 testes, só as 2 falhas conhecidas).

### §34.74 — Progressão Normalizada de Instância: base pro futuro modo Battlefield (31/07/2026)

Discussão levantada pelo usuário (não implementação direta): campos de
batalha estilo MOBA precisam de balanceamento — dois personagens de
levels/equipamentos reais muito diferentes não podem entrar na mesma
partida competitiva. Pesquisa feita (LoL/Dota/HotS/WoW) confirmou que
"nível que reseta a cada partida, sem afetar o personagem persistente"
é padrão comum (champion level do LoL, hero level do Dota) — mas o
modelo do usuário é um HÍBRIDO próprio: reset total (como LoL/Dota) +
progressão de talento reaproveitando a MESMA árvore real da classe
(demonstração de talentos ainda não desbloqueados no mundo aberto),
combinado com skill unlock automático por level (V1: 1 fixa por level,
escolha do jogador estilo LoL fica pra depois) e loja de instância com
itens reais curados. Nenhuma das 3 referências pesquisadas tem esse
formato exato.

**Existia doc anterior conflitante**: `next_implementations/
battlefield_design.md`, §2, já tinha decidido o OPOSTO ("Modelo 1: leva
tudo do personagem principal" — mantém level/itens/talentos reais,
balanceia via bracket de matchmaking + buffs temporários). Essa decisão
foi **superada** por esta (usuário confirmado via pergunta direta) — o
doc tem uma nota de addendum na §2 apontando pra cá; o resto do doc
(fila/brackets, moeda Honra, jungle boss, itens exclusivos, quests)
segue válido como referência futura, fora do escopo desta entrega.

**Escopo desta entrega** (confirmado com o usuário): SÓ o mecanismo de
progressão normalizada em si (level/talento/skill/gold/itens da
instância + snapshot/restore + toggle por modo). Fila/matchmaking/moeda
de instância ficam pra depois. Fica **inerte** — nenhum processador de
jogo chama os hooks ainda, porque o processador do Battlefield em si
(fila, times, mapa) é trabalho futuro separado.

**Decisões fechadas com o usuário:**
1. Talento de instância reaproveita a MESMA árvore/build real da classe
   (`content/talent_data.py::TALENTS`) — funciona de verdade dentro da
   instância, 100% descartado ao sair, nunca converte em nada
   permanente.
2. Tabela nova por classe de "level de instância → skill" (V1: 1 fixa
   por level, sem escolha — `content/skill_config.py::
   INSTANCE_SKILL_UNLOCK_ORDER`).
3. Loja de instância reaproveita itens REAIS de `content/item_table.py`,
   lista curada menor (`content/instance_shop.py::
   INSTANCE_SHOP_ITEM_IDS`, 13 itens).
4. Overlay 100% temporário — restaura o estado real persistente
   inteiro ao sair, sem exceção.
5. Level cap da instância = 15.
6. Só se aplica ao futuro modo Battlefield — Arena continua exatamente
   como está hoje (`_reset_combat_resources` inalterado).
7. Retrátil/opt-in por modo de jogo — toggle `progression_mode`.
8. Level 1 já concede a 1ª skill (não começa sem nenhuma ativa).
9. XP além do cap é descartado, sem banking.
10. Hotbar/keybinds de instância resetam vazios e auto-preenchem
    (não herdam o layout real do jogador).

**Lacuna real achada durante a implementação (não estava no plano
original)**: resetar só `CharacterStats.level/current_xp` não bastava —
os atributos brutos (`strength/intelligence/agility/vitality/defense`,
que crescem a cada level REAL via `CLASS_LEVEL_GAINS`,
`engine/stats_system.py`) alimentam `CombatStats` via
`apply_char_stats_to_combat(char, cs, perm)` independente do `level`.
Sem resetá-los junto, um personagem de level alto manteria os
atributos reais dentro da instância e a normalização não faria efeito
nenhum. Confirmado com o usuário — `InstanceProgressionSnapshot` ganhou
os 5 campos extras, reset vai pro piso de `CLASS_BASE_STATS[class_id]`
(nível 1), crescimento durante a instância usa a MESMA
`CLASS_LEVEL_GAINS` (mesma proporção do jogo real, só que 1x por level
de instância em vez de 1x por level real). `PermanentStats` (bônus
permanente roguelike de morte) NÃO entra no reset — mecânica morta,
personagem não reseta level ao morrer há tempos (confirmado pelo
usuário).

**Segundo bug real achado antes de escrever os testes**: metade das
entradas de `INSTANCE_SKILL_UNLOCK_ORDER` são skills desbloqueadas por
TALENTO no jogo real (ex.: `punho_no_queixo`, `escudo_fogo`) —
`world_systems.is_skill_authorized()` pra essas ignora
`PlayerSkills.learned_skill_ids` completamente, só olha
`TalentTree.allocated`. Conceder a skill só via `learned_skill_ids`
nunca autorizaria de verdade. Corrigido em `_grant_instance_skill()`
(`server/instance_progression.py`): também bumpa
`tt.allocated[tid]` pro mínimo exigido, mesma correção que
`tests/helpers.py::authorize_skill()` já fazia pra fixtures de teste.

**Implementação:**
- `engine/components.py::InstanceProgressionSnapshot` (dataclass nova)
  — guarda o estado real (nível/xp/atributos brutos escalares +
  referências de `TalentTree`/`PlayerSkills`/`Wallet`/`Inventory`/
  `Equipment`) enquanto o player está na instância. Presença do
  componente É o flag "está em progressão normalizada".
- `server/instance_progression.py` (módulo novo) —
  `enter_normalized_progression(ws, eid)` / `exit_normalized_progression
  (ws, eid)` (idempotente) / `is_in_normalized_progression(ws, eid)` /
  `grant_instance_xp(ws, eid, amount)` (seam pro futuro XP de
  minion/jungle/kill — não chamado por nada ainda). `ws` é qualquer
  objeto com `.world` + `._apply_talent_modifiers(eid, allocated)` +
  `._apply_equipment_modifiers(eid)` (na prática, sempre `WorldServer`)
  — reaproveita esses 2 métodos já existentes (login/EQUIP_SYNC/
  TALENT_UPDATE) em vez de reimplementar a aplicação de modifiers de
  talento/equipamento em `CombatStats`.
- Toggle por modo: `mode_cfg.get("progression_mode", "real")` — default
  `"real"` sempre que ausente. `ARENA_MODES` (`server/match_processor.py`)
  não ganhou a chave — Arena fica inerte por padrão, não por um branch
  que alguém possa esquecer de excluir. Um futuro processador de
  Battlefield deve chamar `enter_normalized_progression` logo após
  `transfer_player`+entrada no time/Faction (mesmo ponto que
  `request_arena_accept` já faz pra Arena) e
  `exit_normalized_progression` incondicionalmente no ponto único de
  saída por-jogador (equivalente a `_arena_leave_now`) — ver docstring
  do módulo pros 2 pontos de integração exatos.

Testes novos: `tests/test_instance_progression.py` (13 testes) —
round-trip completo (entra/sai restaura EXATAMENTE, mesma identidade de
objeto onde aplicável, inclusive com estado real não-trivial pré-setado
pra provar que não é um no-op level1→level1), level-up até o cap sem
overflow (+3 pontos de talento por level, não o +1 real), XP além do
cap descartado, todas as skills da ordem concedidas até o cap,
`is_skill_authorized` funcionando contra os componentes trocados sem
NENHUMA mudança na função em si (incluindo o caso talento-gated,
regressão do bug achado acima), simetria de modifier de talento
(aloca ponto → `parry_rating` reflete o efeito → sai → reverte
exatamente), idempotência de saída sem entrada, validação de dados
(todo id de `INSTANCE_SKILL_UNLOCK_ORDER`/`INSTANCE_SHOP_ITEM_IDS`
existe no catálogo real e é da classe certa). Suíte completa 3x limpa
(688 testes, só as 2 falhas pré-existentes conhecidas).

#### §34.74.1 — Mapa de teste + gancho de debug (01/08/2026)

Usuário desenhou `maps/moba_battleground.png` (100×100, cores do
mapping já existente do jogo) pra validar rotas de minion + torres +
progressão normalizada, sem esperar o modo Battlefield de verdade
(fila/matchmaking) ser construído. Convertido via `tools/png_to_map.py`
(já existente, não precisou de mudança) → `maps/moba_battleground.csv` +
`maps/moba_battleground_entities.json` (20 torres, 6 lanes de minion
com `lane_id` top/mid/bot por time — sem isso todas as 3 lanes do MESMO
time compartilhariam 1 timer só, ver §34.73.2).

**Falso alarme investigado e descartado**: primeiro teste de
pathfinding (A* real, `PathfindingSystem`) deu `None` nas 3 lanes —
suspeita inicial (errada) foi cobertura de árvore (`t`, `is_solid=True`,
quase metade do mapa) bloqueando passagem. Causa real: o teste rodou
com os portões FECHADOS (`D`, `ARENA_GATE_TILE`, sólido até abrir) —
spawn/alvo de cada lane ficam num bolsão de 25 tiles isolado atrás do
próprio portão do time, então SEM abrir o portão nenhuma rota é
alcançável mesmo (comportamento correto/esperado antes do combate
liberar). Reteste com portões abertos manualmente: as 3 lanes acham
caminho limpo (~85-90 tiles). As lanes de grama que o usuário desenhou
(8 tiles de largura, com waypoint extra no top/bot pra contornar as
áreas de árvore) sempre estiveram corretas — erro de diagnóstico meu,
não do mapa.

**Gancho de debug** (`server/debug_battleground.py`, NOVO — infra de
teste descartável, zero acoplamento com Arena): comando de chat
`/testbg a|b|leave`, interceptado em `SessionManager._handle_chat`
(`server/session.py`) ANTES de virar mensagem de chat de verdade.
`/testbg a`/`b` carrega uma instância COMPARTILHADA (1 por processo de
servidor, não 1 por jogador) via `_load_instance`, atribui `Faction`,
teleporta pro bolsão do time, e chama
`enter_normalized_progression(ws, eid)` (§34.74) — testa os dois
sistemas junto, como pedido. Portão (tiles `D` locais a este módulo,
`DEBUG_BG_GATE_TILES` — NÃO generaliza `ARENA_GATE_TILES` da Arena,
separação deliberada) abre sozinho 15s depois do primeiro player
entrar, só então chama `_activate_minion_lanes` (mesma regra da Arena:
nunca antes do portão abrir, senão minion nasceria durante o preparo).
`/testbg leave` chama `exit_normalized_progression`, remove `Faction`,
restaura posição real; descarrega a instância quando os dois times
ficam vazios.

Limitação cosmética conhecida e aceita (feature de teste, não
produção): cliente não recebe evento de "portão abriu" —
`ARENA_GATE_OPEN` é hardcoded pra `ARENA_GATE_TILES`
(`client/arena_handlers.py`), não genérico — então o cliente pode
continuar renderizando a célula do portão como fechada mesmo depois de
aberta de verdade no servidor (movimento/pathfinding de minion não são
afetados, só o visual até trocar de mapa/relogar).

Validado via smoke test manual (não entra na suíte automatizada —
feature de debug, descartável): `/testbg a` reseta level real→1,
atribui Faction, teleporta pra instância; portão força-aberto +
`_activate_minion_lanes` sem exceção; `/testbg leave` restaura level
real exatamente, remove Faction, volta pro mapa original.

#### §34.74.2 — INCIDENTE REAL: personagem de teste perdeu level 23→1 (01/08/2026)

Usuário testou `/testbg a` em jogo real — cliente caiu com
`FileNotFoundError` em `load_map_csv` (`engine/map_loader.py`) tentando
abrir literalmente `maps/moba_battleground.csv::debugtest` como nome de
arquivo. Ao reconectar, o personagem "Juugo" (level real 23) apareceu
como level 1 **permanentemente** — sem recuperação possível (checado
`git log -- data/game.db`: último commit é de 15/07/2026 com Juugo em
level 4, 17 dias de progresso não commitado entre esse commit e o
level 23 real — muito longe pra recuperar via git).

**Causa raiz #1 (crash do cliente)**: `_enter()` (`server/
debug_battleground.py`) mandava `DEBUG_BG_INSTANCE_KEY` (a chave
sintética `"maps/moba_battleground.csv::debugtest"`) como `map_file` do
`ZONE_CHANGE` — mas o cliente SEMPRE carrega só o TEMPLATE puro do
disco (`load_map_csv` não entende `"::"`); a instance_key é conceito
100% server-side (diferencia bundles/`MapLocation`/AOI). Confirmado o
padrão correto em `match_processor.py:290`
(`_map_file = self._template_file_of(match["instance_key"])` — Arena
já fazia esse "de-para" antes de montar `ARENA_MATCH_START`, eu
esqueci de replicar). Corrigido: `zone_change["map_file"]` agora usa
`DEBUG_BG_TEMPLATE` (o path puro), nunca `DEBUG_BG_INSTANCE_KEY`.

**Causa raiz #2 (perda de dado — a séria)**: o cliente caiu ANTES do
jogador rodar `/testbg leave`, então `exit_normalized_progression`
nunca rodou. O disconnect handler
(`server/session.py::SessionManager.on_disconnect`) salva o personagem
com o que quer que esteja anexado ao entity NAQUELE momento — como o
entity ainda estava com os componentes da progressão NORMALIZADA
(level 1, `TalentTree`/`PlayerSkills`/`Wallet`/`Inventory`/`Equipment`
vazios), isso foi gravado no banco como se fosse o personagem real.
**Esse bug não era específico do `/testbg`** — é uma lacuna genérica de
qualquer chamador de `enter_normalized_progression` que não tenha um
hook de disconnect: exatamente a mesma classe de bug que Arena já tinha
resolvido pra si mesma (`end_matches_of` chamado ANTES do save em
`on_disconnect`, comentário already existente ali: "sem isso... próximo
login caía... bug real relatado pelo usuário 20/07/2026" — mesmíssimo
padrão, dessa vez descoberto por um incidente de verdade em vez de
revisão de código).

**Correção (2 camadas, defesa em profundidade)**:
1. `server/debug_battleground.py::on_disconnect(ws, eid)` — nova função,
   chamada incondicionalmente do disconnect handler ANTES do save;
   restaura progressão real + remove `Faction` + limpa bookkeeping do
   módulo (`_state["members"]`/`_state["return_pos"]`) + descarrega a
   instância se ficar vazia. Mesmo formato de `_leave()`.
2. `server/session.py::on_disconnect` — logo após `end_matches_of`
   (mesmo ponto, mesmo motivo), chama `debug_battleground.on_disconnect`
   E, adicionalmente, um check GENÉRICO
   (`is_in_normalized_progression`/`exit_normalized_progression` direto
   de `server/instance_progression.py`) — protege qualquer FUTURO
   chamador de progressão normalizada (ex.: o processador real do
   Battlefield) mesmo que esqueça de implementar seu próprio hook de
   disconnect. Regra nova pro projeto: **todo caller de
   `enter_normalized_progression` precisa de um hook de disconnect
   ANTES do save, não só entrada/saída manual** — a defesa genérica em
   `on_disconnect` cobre isso por padrão, mas cada processador ainda
   deve limpar seu PRÓPRIO bookkeeping (Faction/membership/instância)
   como `debug_battleground.on_disconnect` faz.

Validado via smoke test manual: personagem real level 23 → entra na
instância (level 1) → simula disconnect SEM `/testbg leave` →
`on_disconnect` restaura level 23 exatamente, remove `Faction`, zera
bookkeeping — incidente não se repete. Suíte completa 3x limpa (688
testes, só as 2 falhas pré-existentes conhecidas) depois da mudança em
`session.py::on_disconnect`.

**Lição pro histórico**: `data/game.db` não é commitado com frequência
(17 dias de progresso perdidos entre commits) — fora do escopo deste
incidente, mas vale considerar algum backup/snapshot periódico do banco
se personagens de teste real (não só de dev) começarem a acumular
progresso importante.

**Recuperação parcial**: o personagem afetado era "jungo" (não "Juugo",
outro personagem diferente — confundi os dois inicialmente). Achado no
commit `3b5921b` (15/07/2026) com level 22 + inventário/equipamento/
talentos/skills/quests completos — restaurado no `data/game.db` ao vivo
(backup do estado corrompido guardado antes da escrita), preservando
`char_stats_json` atual (coluna nova desde 15/07, não afetada pelo
incidente). Progresso entre 15/07 e o incidente (se houve) continua
perdido — recuperação chega perto do level 23 relatado, não é 100%.

#### §34.74.3 — 2 ajustes de playtest no battleground de teste (01/08/2026)

Primeiro playtest de verdade do usuário no mapa MOBA, depois dos
incidentes acima corrigidos:

1. **Sem contagem visível do portão** — sem saber quando abre, não dava
   pra explorar o mapa esperando a wave de minion. Reaproveitado o
   overlay cosmético que a Arena já tem
   (`client/arena_handlers.py::_draw_arena_countdown_overlay`, só
   depende de `_arena_countdown_deadline_val`, sem gate de "está numa
   partida de arena") — `debug_battleground.handle_command` agora
   retorna uma 3ª posição (`countdown_remaining`), `session.py::
   _handle_chat` manda `ARENA_COUNTDOWN {remaining}` antes da resposta
   de chat. Contagem é DO PORTÃO (não por-jogador — quem entra depois
   do 1º vê o tempo já menor, mesmo espírito da Arena real).

2. **Minions da wave travando uns nos outros ao nascer** — os 7
   minions de `_tick_minion_waves` nasciam todos no MESMO tick
   (mesmo com os offsets de posição de `_MINION_WAVE_OFFSETS`
   espalhando os tiles) — `MinionSystem` via os vizinhos recém-nascidos
   como `dynamic_obstacle` antes de terem espaço pra sair do
   aglomerado, travando. Pedido do usuário: escalonar o nascimento em
   0.5s entre um minion e outro da mesma wave. Implementado como fila:
   `_tick_minion_waves` agora só CALCULA a rota dos 7 e enfileira em
   `self._minion_spawn_queue` (novo dict de estado no `WorldServer`,
   junto de `_minion_lanes`/`_minion_wave_timers`) com um `delay`
   crescente (`(offset_idx-1) * MINION_SPAWN_STAGGER_S`); nova função
   `_tick_minion_spawn_queue(dt)` (chamada logo depois de
   `_tick_minion_waves` no tick principal) drena a fila e só aí chama
   `create_minion` de fato, um por vez, conforme cada `delay` zera.
   `MINION_SPAWN_STAGGER_S = 0.5` — constante de classe em
   `WorldServer`, junto de `_MINION_WAVE_COMPOSITION`/
   `_MINION_WAVE_OFFSETS`.

   Testes de `tests/test_minions.py::TestMinionWaveSpawning` precisaram
   de 2 ajustes pra continuar válidos com o spawn escalonado: (a)
   `wave_interval_s` de teste subiu de 1.0s pra 6.0s (com stagger de até
   3.0s pro 7º minion, um intervalo de 1.0s fazia a 2ª wave disparar
   ANTES da 1ª terminar de nascer — waves se sobrepunham, quebrando as
   asserções de composição exata); (b) `run_ticks` de 30→200 (tempo
   suficiente pra fila de spawn esvaziar). Teste novo dedicado,
   `test_minions_da_wave_nascem_escalonados_nao_todos_no_mesmo_tick`,
   confirma que a contagem de minions cresce aos poucos ao longo dos
   ticks (não pula de 0→7 num tick só). Suíte completa 3x limpa (689
   testes, só as 2 falhas pré-existentes conhecidas).

#### §34.74.4 — Portão não abria pro cliente + minion travado atrás de torre (01/08/2026, mesmo dia)

Segundo round de playtest do usuário no battleground de teste: (1)
contagem chegou a 0 mas o portão não abriu — não dava pra explorar; (2)
minions travados atrás de uma torre, "um deles tem caminho pra andar
mas não desvia".

**Bug 1 — minion travado, raiz real era rota cruzando torre**: a rota
de cada minion é calculada 1x em `_tick_minion_waves` (A* livre, só
terreno — sem saber onde as torres estão). Se o caminho reto cruza o
tile exato de uma torre, esse tile vira o PRÓXIMO PASSO da rota em
ADVANCING — e `_walk_toward` só pede ao pathfinder "chegar no PRÓXIMO
tile" (distância 1): sem espaço nenhum pra desviar quando esse único
tile está permanentemente ocupado (torre nunca sai do lugar → nunca
destrava sozinho). Corrigido na ORIGEM: `WorldServer.
_get_tower_tiles_for_map(map_file)` (novo) coleta os tiles de toda
torre viva no mapa/instância e entra como `dynamic_obstacles` nas
chamadas de `pathfinding.find_path` dentro de `_tick_minion_waves` — a
rota nunca mais atravessa o tile exato de uma torre. Perto de uma
torre pra brigar de verdade continua funcionando normal (FIGHTING
persegue por chebyshev, nunca tenta pisar no tile exato do alvo).
Teste novo com prova de regressão (revertido manualmente, confirmado
que falha sem o fix, restaurado):
`test_rota_da_wave_desvia_de_torre_no_caminho_reto`.

**Bug 2 — portão nunca avisava o cliente**: já era uma limitação
conhecida/documentada (§34.74.3) que virou bug de verdade no playtest.
`debug_battleground._tick` só trocava o `tile_matrix` do SERVIDOR — sem
enviar nada ao cliente, que continuava renderizando a célula fechada
pra sempre. Corrigido generalizando `ARENA_GATE_OPEN`
(`client/arena_handlers.py::_handle_msg_arena_gate_open`): aceita
`payload["gate_tiles"]` opcional (lista `[[x,y],...]`) — se presente,
usa esses tiles em vez do `ARENA_GATE_TILES` hardcoded; ausente (Arena
real, que manda `{}`) = comportamento de sempre, sem mudança. Servidor
ganhou o MESMO padrão de buffer-por-tick que a Arena já usa:
`debug_battleground._state["pending_gate_open_eids"]` (populado no
`_tick` quando o portão abre) +
`drain_gate_open_notifications()` (consumida em `server/session.py`,
mesmo ponto de `consume_arena_gate_open_events`) — **e** entrou no
`has_pending` de `SessionManager._on_tick` (regra do CLAUDE.md: todo
buffer novo consumido no dispatch tem que entrar em `has_pending` no
MESMO commit, senão fica preso até atividade alheia destravar —
exatamente a classe de bug que já aconteceu com Arena/grupo/duelo
antes). Validado via smoke test manual: portão abre →
`pending_gate_open_eids` populado → drenado → `gate_tiles` correto no
payload (18 tiles, os 2 segmentos em L dos 2 times).

**INCIDENTE — 2º `git checkout --` sem `git status` na mesma sessão**:
durante a investigação, testei o fix da torre revertendo temporariamente
com `sed` pra confirmar que o teste novo falha sem o fix (técnica
correta) — mas pra desfazer o `sed` rodei `git checkout --
server/world_server.py` sem checar `git status` antes. Isso não desfez
só o `sed`: reverteu o arquivo INTEIRO pro último commit
(`ba8d6de`, "Sistema de Torres v0.29.0", 29/07/2026) — apagando TODA a
integração do Sistema de Minions em `WorldServer` (que nunca foi
commitada: `_minion_lanes`, `_minion_wave_timers`, `_tick_minion_waves`,
`_create_minion_lanes`, `_activate_minion_lanes`,
`_get_pathfinding_for_map_file`, `_compute_team_avg_level`,
instanciação de `MinionSystem`, ramo de `is_ranged` de Minion em
`_build_mob_spawn_payload`) MAIS as 3 mudanças de hoje (spawn
escalonado, obstáculo de torre, chamadas no tick principal). Mesmo erro
já cometido uma vez antes nesta sessão (§ anterior, arquivo
`engine/world_systems.py`) — reconstruído de memória de novo, desta vez
com um oráculo melhor: rodar a suíte de testes imediatamente revela
EXATAMENTE o que falta (`AttributeError: 'WorldServer' object has no
attribute '_minion_lanes'`, etc.), guiando a reconstrução método por
método em vez de depender só de memória solta. Suíte completa 3x limpa
(690 testes, só as 2 falhas pré-existentes) confirma reconstrução
completa e correta. **Lição reforçada**: `git checkout --` NUNCA sem
`git status` antes, mesmo pra "desfazer só uma mudança pequena" — o
comando não sabe diferenciar "minha mudança de teste" de "todo o
trabalho não commitado do arquivo".

#### §34.74.5 — Terceiro round de playtest: 5 bugs corrigidos (01/08/2026, mesmo dia)

Usuário testou de novo depois dos fixes de portão/torre/spawn escalonado
e reportou mais 5 problemas reais (2 específicos do battleground de
teste, 3 gerais do jogo):

1. **Level do player não aparecia 1 no client** — `enter_/
   exit_normalized_progression` mutava `CharacterStats.level` só no
   servidor; não existia NENHUM canal que empurrasse `level` pro dono
   mid-session (`queue_stats_update`/`STATS_UPDATE` não tinha esse
   campo no schema; o único S→C que carregava `level` — dirty-check de
   HP pra nameplate remoto — exclui explicitamente o próprio dono). O
   level-up normal (por XP) nunca precisou disso porque o CLIENTE
   re-deriva o level sozinho a partir de `xp_gained`
   (`process_levelups` roda IGUAL dos dois lados) — mas aqui não há XP
   nenhum "ganho", é um OVERRIDE direto. Corrigido: `level` novo no
   schema de `queue_stats_update` (`server/world_server.py`), client
   aplica direto em `CharacterStats.level` sem passar por
   `process_levelups` (`client/network_handlers.py::
   _handle_msg_stats_update`), e `server/instance_progression.py`
   ganhou `_push_stats_update()` (chamado no fim de `enter_`/
   `exit_normalized_progression`) mandando `level`+`hp`/`hp_max`
   (já recalculados pelos `_apply_talent_modifiers`/
   `_apply_equipment_modifiers` que já rodavam)+`gold`+`talent_points`.
   **Gap conhecido, não coberto**: hotbar/skills completas, alocação de
   talento completa, conteúdo de inventário/equipamento — sem
   mecanismo de push em massa pra isso (só aparece certo no próximo
   login). Testes novos: `tests/test_instance_progression.py::
   TestInstanceProgressionClientSync`.

2. **Minions travavam quando um player ALIADO parava no caminho** —
   mesma classe de bug já corrigida pra torre (§34.74.4), mas essa não
   dava pra resolver excluindo da ROTA (torre nunca se move, mas um
   player pode parar em QUALQUER tile a qualquer momento — não dá pra
   prever no cálculo da rota, feito 1x no spawn da wave). Raiz: em
   ADVANCING, o destino de movimento era sempre o PRÓXIMO TILE da rota
   (distância 1) — `_walk_toward` exclui o destino de
   `dynamic_obstacles` (senão o A* rejeitaria o destino inteiro), mas
   com destino a 1 tile de distância não sobra espaço NENHUM pra
   desviar; se esse único tile fica ocupado, o passo de execução
   (`is_tile_walkable`) rejeita pra sempre. Corrigido: ADVANCING agora
   mira o PRÓXIMO CHECKPOINT (até 5 tiles à frente, múltiplo de 5),
   dando ao A* espaço real pra contornar — mesmo padrão que RETURNING
   já usava (chegada por ÁREA, `CHECKPOINT_ARRIVAL_RADIUS`). Sutileza
   achada em teste: chegada por ÁREA só vale pra checkpoint
   INTERMEDIÁRIO — no ÚLTIMO tile da rota (base inimiga) exige chegada
   EXATA (raio 0), senão `route_idx` pula pra `len(route)-1` (trava a
   condição de movimento) enquanto o minion ainda está fisicamente 1-2
   tiles longe, parando pra sempre sem nunca terminar a rota de
   verdade. Teste novo com prova de regressão (2 achados durante a
   escrita do teste: sem `MapLocation` no bloqueador ele fica invisível
   pro pathfinder; sem chamar `TileValidationSystem.update()` no loop
   de teste o cache de ocupação nunca é reconstruído e o bloqueio nunca
   é aplicado de verdade — os dois corrigidos no próprio teste antes de
   confirmar a regressão): `test_minion_contorna_player_aliado_parado_
   no_caminho`.

3. **Torres não atacavam minions do time oposto** — `TowerSystem.
   _acquire_target` classificava candidatos em 2 buckets
   (`PlayerControlled` / `AIControlled`-ou-`NPC`) — `Minion` não tem
   NENHum dos dois (`MinionSystem` próprio, sem `AIControlled`, mesma
   decisão de `Tower`), então passava em TODOS os checks de
   hostilidade/alcance/LOS mas nunca entrava em bucket nenhum, nunca
   virando alvo. Corrigido: bucket "não-player" virou o `else` default
   (qualquer hostil que não seja `PlayerControlled`), cobrindo Minion
   de graça. Teste novo: `test_torre_ataca_minion_hostil_no_alcance`
   (`tests/test_towers.py`).

4. **Barra de HP de mob/torre/minion não ficava verde pro time aliado**
   — `client/remote_entity_handlers.py::_draw_mob_hp_bars` sempre
   comparava a facção do mob contra o `PLAYER_FACTION` genérico
   hardcoded, NUNCA contra o time real do jogador dentro de uma
   instância — `RELATIONSHIP` já tinha entrada explícita hostil pra
   `("arena_time_a","arena_time_b")` (adicionada de propósito pra isso,
   ver §34.70), mas nunca era consultada porque o 2º lado da comparação
   sempre estava errado. Corrigido na raiz, não com uma variável
   especial: `client/arena_handlers.py::_handle_msg_arena_countdown`
   (que já recebe `my_faction` no payload, ver item §34.74.4) agora
   ANEXA um `Faction` de verdade no player LOCAL (mesma coisa que o
   servidor já faz do lado dele) — `get_entity_faction` (`engine/
   faction_system.py`) já prioriza um `Faction` real se presente, então
   isso corrige `is_hostile`/`can_engage`/`get_relationship_between`
   pro player local de UMA VEZ, sem precisar de gambiarra em cada
   ponto que precisa saber "sou aliado ou hostil disso" (barra de HP,
   TAB/SPACE — item 5 abaixo). Removido em `client/network_handlers.py::
   _handle_msg_zone_change` (qualquer troca de mapa encerra o contexto
   de time). `_draw_mob_hp_bars` passou a usar `get_relationship_between`
   direto, igual o resto do código já faz, em vez de reimplementar a
   comparação.

5. **TAB/SPACE podiam selecionar aliado** (pedido explicitamente como
   problema GERAL, não só do battleground) — `ui/systems.py::
   _visible_enemies_sorted`'s loop de `Enemy` (mobs/torres/minions
   remotos são TODOS tagueados `Enemy` do lado do cliente,
   `create_enemy` genérico) não filtrava hostilidade NENHUMA; `client/
   save_sync_handlers.py::_space_engage_online` (SPACE online, o path
   que realmente roda em jogo — não o `_space_engage` offline, que já
   filtrava) iterava `self._remote_mobs` inteiro sem filtro nenhum
   também. Corrigidos os dois com `is_hostile` (não `can_engage` — esse
   também libera "neutro", e o pedido é hostil de verdade). O loop de
   `RemoteControlled` (players remotos) dentro de `_visible_enemies_
   sorted` foi DELIBERADAMENTE mantido em `can_engage` — permissão de
   PvP entre players é CONTEXTUAL (duelo/arena/zona,
   `_pvp_context_resolver`), um oponente de duelo normalmente está na
   MESMA facção default ("jogadores" = amigavel por padrão), só o
   contexto libera o engajamento; trocar pra `is_hostile` ali quebraria
   TAB durante duelo (nunca resolveria hostil por tier de facção fixo).

Suíte completa 3x limpa (694 testes, só as 2 falhas pré-existentes
conhecidas) depois dos 5 fixes.

#### §34.74.6 — Economia de instância: loja, ouro por kill, XP só de player (01/08/2026, mesmo dia)

Últimos 3 pedidos do usuário na mesma rodada de playtest, decisões de
design fechadas via `AskUserQuestion` antes de implementar (não
perguntar de novo):

- **Moeda**: reaproveita `Wallet.gold` (já isolado/resetado pelo
  overlay de progressão normalizada — decisão do usuário, "reaproveitar
  gold isolado" em vez de criar campo "Honra" separado). `INSTANCE_
  STARTING_GOLD` subiu de 0 pra **150** (`server/instance_progression.py`).
- **UI do inventário de 6 slots**: NENHUMA UI nova — decisão do usuário
  foi reaproveitar a bag normal do jogo (tecla B), que já renderiza
  qualquer `Inventory` anexado; como a instância já troca pra um
  `Inventory(max_slots=6)` (§34.74, decisão #6 original), a bag mostra
  6 slots automaticamente sem nenhum código de UI novo.

**Loja de instância** — vendedor reaproveitando 100% a infraestrutura
de merchant já existente (`content/merchant_data.py::SHOPS`,
`engine/map_loader.py::"merchants"`, `WorldServer.process_shop_buy`/
`process_shop_sell`):
- `SHOPS["instance_shop"]` novo (`content/merchant_data.py`) — estoque
  = os 13 ids de `content/instance_shop.py::INSTANCE_SHOP_ITEM_IDS`
  (já existia, nunca tinha sido conectado a nada), preços iniciais
  hand-picked (ajustável).
- 2 vendedores adicionados em `maps/moba_battleground_entities.json::
  merchants` (um por base, `shop_id: "instance_shop"`).
- **Venda de volta**: `process_shop_sell` já é genérico (qualquer item
  no `Inventory`, não restrito por `shop_id`/posição) — zero código
  novo precisou pra "o jogador pode vender os itens desse inventário
  de novo".
- **Bug latente achado e corrigido nesse processo**: `process_shop_buy`
  validava espaço no inventário contra um **"24" hardcoded** (linha do
  passo 3), dessincronizado do `inv.max_slots` REAL que o passo 6 (grava
  o item) já respeitava de verdade — com um inventário de 6 slots
  (instância), isso deixava passar uma compra que o passo 6 depois
  descartava em silêncio (`break` ao bater o teto real de 6), cobrando
  o gold sem entregar o item. Corrigido: passo 3 agora lê
  `inv.max_slots` do `Inventory` vivo, igual o passo 6 já fazia.

**Ouro por kill, só pro killer** — pedido explícito: "só quando dá o
dano que MATA", nunca compartilhado/proporcional (diferente de XP).
Usa `PendingDeath.killer_entity_id` (golpe final), **nunca**
`first_attacker_eid` (quem bateu primeiro — é o dono do LOOT/quest,
conceito diferente, já existia). Novo:
- `Minion` ganhou `gold_min`/`gold_max` (componente + `MINION_TABLE`,
  espelhando o que `Tower` já tinha) — `create_minion` passa adiante.
- `server/instance_progression.py::grant_instance_gold(ws, eid, amount)`
  — soma em `Wallet.gold` + push de `STATS_UPDATE`; no-op fora de
  progressão normalizada.
- `INSTANCE_PLAYER_KILL_GOLD = 50` (constante, matar PLAYER inimigo —
  não tem uma tabela de definição própria como minion/torre).
- Hook novo em `server/server_death_handler.py`: se `killer_eid` é
  player E está em progressão normalizada, concede gold_min/max do que
  morreu (torre/minion) ou `INSTANCE_PLAYER_KILL_GOLD` (player) — SEM
  tocar no `coins`/loot do mundo aberto, que continua exatamente igual
  (torre real, Arena, etc.).

**XP só entre players (decisão #8 do usuário)** — 2 gaps reais achados
e corrigidos:
1. `damage_log.items()` (proporção de XP por dano) nunca filtrava
   ATACANTE por ser player — minion/torre não ganhavam XP visível
   (sem `CharacterStats`), mas o dano deles ainda ENTRAVA no
   denominador da proporção, diluindo o que os players recebiam (mob
   50% morto por minion + 50% por player dava só METADE do XP pro
   player). Corrigido: filtra `damage_log` pra só `p_eid` que é
   player de verdade (`self.world_server._player_eids.values()`) ANTES
   de somar `total_damage` — o player passa a receber o XP CHEIO.
   Fallback "killer leva tudo" (sem damage_log) ganhou o MESMO filtro
   (senão um minion que dá o golpe final sem nenhum player ter batido
   virava "player_eid" da entrada de XP). Teste com prova de
   regressão (revertido, confirmado que falha sem o fix, restaurado):
   `test_dano_de_minion_nao_dilui_xp_do_player`.
2. **XP de instância agora É concedido de verdade em combate** (antes,
   `grant_instance_xp` era só um seam nunca chamado) — `server/
   world_server.py`'s loop de consumo de `consume_xp()` agora checa
   `is_in_normalized_progression(self, player_eid)` PRIMEIRO: se
   verdadeiro, desvia inteiro pro `grant_instance_xp` (curva/cap/taxa
   de instância) e `continue`, pulando TODO o resto do bloco real —
   isso é deliberado e crítico: o resto do bloco (`process_levelups`
   real, re-aplicação de talento, e principalmente o **save-to-DB
   "crash não perde progresso"**) NUNCA pode rodar pra um player em
   progressão normalizada, senão salvaria o estado RESETADO da
   instância como se fosse o personagem real — mesma classe do
   incidente de disconnect já documentado em §34.74.2.

Testes novos: `tests/test_minions.py::TestMinionDeathXpGold::
test_gold_de_instancia_vai_pro_killer_dentro_da_progressao_normalizada`/
`test_dano_de_minion_nao_dilui_xp_do_player`. Suíte completa 3x limpa
(696 testes, só as 2 falhas pré-existentes conhecidas).

#### §34.74.7 — Quarto round de playtest: alvo aliado via hotkey, vendedor neutro, anel inequipável, arqueiro sem munição (01/08/2026, mesmo dia)

4 fixes pontuais a partir de novo feedback do usuário testando `/testbg`:

1. **Teclas de atalho de habilidade selecionavam aliado como alvo** — TAB/
   ESPAÇO já filtravam hostilidade (§34.34.4), mas `ui/systems.py::
   _resolve_target` (usado pelas hotkeys 1-7 quando nenhum alvo está
   selecionado) tinha 3 loops de candidato próprios, nenhum filtrado.
   Corrigido: `is_hostile` nos 2 loops de mob (`Enemy`/AIControlled — tier
   estrito, mesmo padrão de `_visible_enemies_sorted`), `can_engage` no
   loop de `RemoteControlled` (permissão de PvP é CONTEXTUAL — duelo/arena
   — não tier de facção fixo; `is_hostile` ali quebraria auto-mirar um
   oponente de duelo de verdade). Testes:
   `tests/test_resolve_target_hostility.py` (prova diferencial: revertido,
   confirmado que falha, restaurado).
2. **Vendedor de instância "neutro"** (atacável sem querer via clique) —
   `civis` (facção dos NPCs de serviço) x `arena_time_a`/`arena_time_b`
   nunca tinha entrada em `RELATIONSHIP`, caía em `DEFAULT_RELATIONSHIP`
   ("neutro" — `can_engage` só bloqueia "amigavel"). Corrigido:
   `content/faction_data.py` ganhou os 2 pares como "amigavel". Teste:
   `tests/test_faction.py::TestGetRelationship::
   test_vendedor_de_instancia_e_amigavel_aos_dois_times`.
3. **Anel comprado não equipava** — causa raiz era `steel_ring`
   (`level_requirement=12`) na lista curada da loja de instância; instância
   começa no level 1 (§34.74, decisão #5), então o item ficava
   permanentemente inequipável até o jogador subir de level DENTRO da
   instância. Trocado por `ring_power` (`level_requirement=1`) em
   `content/instance_shop.py` + `content/merchant_data.py::
   SHOPS["instance_shop"]`.
4. **Arqueiro sem munição vendável** — usuário perguntou diretamente se
   era melhor vender flechas soltas ou uma aljava com capacidade "infinita"
   pra não deixar gestão de munição atrapalhar o PvP da instância.
   Recomendado e implementado: `battleground_quiver` (novo item,
   `content/item_table.py`, 999 flechas — não literalmente infinito, só o
   suficiente pra nunca esvaziar numa partida), exclusivo desta loja
   (substituiu `basic_quiver` em `INSTANCE_SHOP_ITEM_IDS`).

#### §34.74.8 — Sync de Inventory/Equipment de instância + painel HUD dedicado (reverte a decisão de UI do §34.74.6) (01/08/2026, mesmo dia)

Investigando mais a fundo o "anel comprado não equipava" do item 3 acima:
mesmo com o `level_requirement` corrigido, o usuário reportou que os itens
REAIS do personagem continuavam aparecendo na bag durante a instância
("não sei o que vale"), e um item comprado às vezes nem aparecia. Causa
raiz mais profunda, já documentada como gap conhecido em
`instance_progression.py::_push_stats_update` (§34.74.2/§34.25): o
servidor troca `Inventory`/`Equipment` do player (`add_component`) ao
entrar/sair da instância, mas o CLIENTE nunca ficava sabendo — sua cópia
local desses componentes nunca mudava. Resultado: a bag do cliente
continuava mostrando os itens REAIS, e um item comprado dentro da
instância era `.append()`-ado (BUY_RESULT, `client/network_handlers.py`)
no `Inventory` LOCAL ERRADO (o real, não o da instância) — se a bag real
já estivesse cheia, o item comprado nem aparecia visualmente.

**Pedido do usuário, reversão explícita do §34.74.6**: em vez de continuar
reaproveitando a bag normal (tecla I/B), criar um painel HUD NOVO e
dedicado — 6 slots, canto inferior direito, sempre visível durante a
instância — e fazer a compra ir direto pra esse inventário, com a janela
do comerciante também mostrando esse inventário no lugar do normal.

**Decisão de design**: em vez de uma estrutura de dados paralela (estilo
`TradeUIState`), o fix troca o `Inventory`/`Equipment` REAIS do cliente
pelo conteúdo da instância — a MESMA troca que o servidor já faz. Como
`ShopSystem` (loja), `client/inventory_handlers.py` (bag/equipar) e o
painel novo já leem esses componentes ao vivo do `player_entity` sem
cache próprio, a troca corrige TUDO automaticamente — `ShopSystem` não
precisou de nenhuma mudança pra satisfazer "loja mostra o inventário da
instância".

- **Canal de sync**: 4 campos novos no payload de STATS_UPDATE (S→C,
  `shared/messages.py`), emitidos só por
  `instance_progression.py::enter_/exit_normalized_progression` (via
  `_push_stats_update` estendido): `in_instance` (bool), `inv_snapshot`
  (lista de item dicts, formato de `BUY_RESULT`, serializado via
  `WorldServer._item_data_from_obj`), `inv_max_slots`, `equip_snapshot`
  (dict slot→item dict|None). Reaproveita o canal `queue_stats_update`
  já 100% conectado no dispatch por-tick — **sem** criar buffer novo (evita
  a classe de bug de `has_pending` esquecido, ver regra no CLAUDE.md do
  projeto).
- **Cliente**: `client/network_handlers.py::_handle_msg_stats_update`
  aplica os 3 campos de inventário como substituição COMPLETA (não merge
  incremental — mesmo espírito de TRADE_STATE) via `self._item_from_data`
  (já usado por BUY_RESULT/TRADE_STATE); `in_instance` liga/desliga
  `InstanceInventoryUIState.active` (componente novo,
  `ui/ui_components.py`, anexado ao player em
  `engine/entity_factory.py::create_player`).
- **Painel HUD novo**: `client/instance_inventory_panel.py`
  (`InstanceInventoryPanelHandlers`, mixin de `GameEngine` — mesmo padrão
  de `ConsumableBarHandlers`). Renderiza 6 slots ancorados no canto
  inferior direito (só quando `InstanceInventoryUIState.active`), lendo o
  MESMO `Inventory`/`Equipment` que a bag normal (não uma segunda cópia).
  Clique direito num slot ocupado chama `self._equip_item(item)`
  (`client/inventory_handlers.py`, já validava classe/arma/level — zero
  lógica de equipar duplicada).

Testes: `tests/test_instance_progression.py::
TestInstanceProgressionInventorySync` (3 testes, prova diferencial:
`inv_snapshot`/`equip_snapshot`/`in_instance` corretos nos dois sentidos,
item real nunca vaza no snapshot de entrada). Suíte completa 3x limpa (706
testes, só as 2 falhas pré-existentes conhecidas — `test_map_services.py::
TestServiceResolverGuard`, `test_server.py::TestCCGeneralizado`, sem
relação com este trabalho).

#### §34.74.9 — Minion esquece o checkpoint: ao perder o alvo, segue direto em vez de voltar pra trás (02/08/2026)

Pedido do usuário, quinto round de playtest do battleground de teste:
reverte a decisão original de §34.73 (30/07/2026) de o minion voltar pro
ÚLTIMO CHECKPOINT (`route_idx//5*5`, atrás da posição atual) antes de
retomar o avanço ao perder o alvo. Comportamento observado em jogo: o
minion literalmente "voltava pra trás" (backtrack visível) sempre que
terminava um combate, antes de seguir de novo — o usuário quer que ele
apenas continue andando pra FRENTE a partir de onde já está, atacando
qualquer hostil que entrar no raio de aggro pelo caminho (o branch
`ADVANCING` já fazia exatamente isso, só faltava não mais desviar pra
`RETURNING` primeiro).

Fix: `engine/world_systems.py::MinionSystem.update()` — ao perder o alvo
(`FIGHTING` → alvo inválido), transita direto pra `state = "ADVANCING"`
em vez de `"RETURNING"`. O estado `"RETURNING"` (e o branch inteiro que o
processava — backtrack até a ÁREA do checkpoint antes de retomar) foi
**removido** do `MinionSystem`, junto com `Minion.last_checkpoint_idx()`
(ficou sem nenhum chamador). `Minion.state` agora só tem 2 valores:
`ADVANCING`/`FIGHTING`. O conceito de "checkpoint" (múltiplos de 5 de
`route_idx`) continua existindo só como MIRA intermediária do A* dentro
do próprio `ADVANCING` (dar espaço real pro pathfinding contornar um
obstáculo temporário) — não é mais um destino que o minion "volta" pra
trás pra alcançar.

**Nota**: este `RETURNING` é interno de `Minion.state` — não confundir
com `AIControlled.state == "RETURNING"` (evasão/leash de mob comum via
`EnemyAISystem`), que continua existindo normalmente e não foi tocado.

Testes: `tests/test_minions.py::TestMinionSystemTargeting::
test_ao_perder_alvo_minion_segue_direto_sem_voltar_ao_checkpoint`/
`test_apos_perder_alvo_minion_continua_avancando_ate_a_base` (reescritos
— as versões antigas testavam explicitamente o backtrack que foi
removido; prova diferencial: revertido, confirmado que falha, restaurado).
Suíte de minions completa (27 testes) verde.

#### §34.74.10 — XP de minion vira compartilhado por proximidade (não por dano) dentro da instância (02/08/2026)

Pedido do usuário, mesmo round de playtest: "a xp não é para ser
necessário bater no mob, se o mob morrer perto dos players, tem que ser
dividido entre os players, sem necessidade de dar um hit sequer nos
minions" — modelo estilo LoL (creep XP é compartilhado com qualquer
campeão aliado dentro do raio, mesmo sem ter batido; só o GOLD de
last-hit exige o golpe de verdade). Diferente do XP de mob comum
(zumbi/lobo/bandido no mundo aberto), que continua 100% proporcional por
`damage_log` — este novo comportamento é EXCLUSIVO de `Minion` (lane
creep, só existe dentro de progressão normalizada de instância).

Fix: `server/server_death_handler.py`, bloco de cálculo de XP — nova
checagem ANTES do `damage_log` proporcional: se a entidade morta é
`Minion` E existe pelo menos 1 player em progressão normalizada dentro de
`PARTY_XP_SHARE_RADIUS_TILES` (`shared/constants.py`, mesmo raio já usado
pro split de XP de grupo) da posição de morte, TODOS esses players
recebem uma fatia igual de `base_xp` (`max(1, base_xp // len(nearby))`),
e o caminho `damage_log`/fallback-killer é pulado inteiro pra essa morte.
Sem NENHUM player em progressão normalizada por perto, cai no
`damage_log` normal (fallback seguro — minion fora de instância nunca
deveria existir de verdade, mas não quebra se acontecer).

Novo helper reaproveitável: `server/instance_progression.py::
players_in_normalized_progression_near(ws, tx, ty, map_file, radius)` —
mesmo padrão de `PartyProcessorMixin._party_members_in_range`,
generalizado pra QUALQUER player em instância (não só grupo). O bloco
"2c. Party" existente (redistribuição por grupo) continua rodando depois
e funciona igual sobre essas entradas — se os players próximos também
forem do mesmo grupo, a fatia é redistribuída de novo por ele (composição
segura, sem conflito).

**Gold continua exigindo golpe final de verdade** (`killer_eid`, ver
§34.74.6) — não foi tocado por este fix; usuário testou e reportou que
matar um minion pessoalmente não estava dando gold. Investigação não
achou defeito no código (mecanismo unitário testado e confirmado
funcionando via simulação isolada) — suspeita principal é o processo do
servidor rodando código desatualizado (mesmo processo de dev ligado desde
antes dessas mudanças serem escritas nesta sessão); mesma suspeita pro
bug relatado de "compra na loja de instância não funciona" (§34.74.7-.8
mudaram justamente o catálogo dessa loja). Reiniciar o servidor antes de
investigar mais fundo.

Testes: `tests/test_minions.py::TestMinionDeathXpGold::
test_xp_de_minion_e_por_proximidade_sem_precisar_bater`/
`test_xp_de_minion_por_proximidade_e_dividido_entre_players_perto` (prova
diferencial: revertido, confirmado que falha, restaurado).

#### §34.74.11 — Causa raiz real de "não consigo comprar na loja de instância" + respawn automático na base (02/08/2026)

**Compra na loja de instância (bug real, não resolvido nas 2 tentativas
anteriores)**: usuário reportou que o painel "sua bag" dentro da loja
aparecia vazio e a compra continuava não fazendo nada. Investigação a
fundo achou a causa raiz de verdade: `server/session.py::
_handle_buy_request` valida espaço de inventário contra
`session.last_client_payload.get("inventory")` — o último SAVE_STATE que
o CLIENTE mandou — **não** contra o `Inventory` ao vivo. O swap de
instância (§34.74.8) troca `Inventory.max_slots` pra 6 no ato, mas o
cliente só manda um SAVE_STATE novo em pontos específicos (compra,
equipar, etc.) — nada disparava um IMEDIATAMENTE após aplicar
`inv_snapshot`. Resultado: `session.last_client_payload["inventory"]`
continuava com a contagem da bag REAL (ex.: 18 itens) enquanto
`max_slots` ao vivo já era 6 — toda compra caía em `inventory_full`
(`18 + 1 > 6`) na origem, mesmo com a bag da instância genuinamente
vazia (o painel vazio na loja não era o SINTOMA do bug, era a prova de
que o swap client-side funcionava — a causa real estava no cache
server-side desatualizado).

Fix: `client/network_handlers.py::_handle_msg_stats_update` chama
`self._send_save_state()` assim que aplica `inv_snapshot`/
`equip_snapshot` — garante que `session.last_client_payload` aprende o
tamanho/conteúdo novo da bag imediatamente, nos dois sentidos (entrada E
saída da instância). Teste: `tests/test_client_instance_sync.py` (prova
diferencial: revertido, confirmado que falha, restaurado).

**Respawn automático na base (pedido do usuário)**: dentro do
battleground de teste, morrer agora NÃO passa pelo fluxo normal de
"Liberar espírito"/caminhada de fantasma/prompt "Reviver agora?" (esse
fluxo continua 100% intacto pro resto do jogo — PvE normal, Arena) — em
vez disso, `server/debug_battleground.py::_process_respawns` (novo,
chamado a cada tick pelo hook já existente) detecta `GhostState.is_dead`
de um membro, inicia uma contagem de `DEBUG_BG_RESPAWN_S = 15.0`
(`shared/constants.py`) e, ao zerar, teleporta pro spawn do TIME
(`Faction`) via `snap_to_tile` + reaproveita `WorldServer._revive_player`
(mesma rotina de HP/mana/limpeza de corpse/broadcast `PLAYER_REVIVE` do
revive manual — nenhuma lógica de revive duplicada). Cliente:
`client/death_ui_handlers.py::_render_death_modal` detecta
`InstanceInventoryUIState.active` (MESMO flag do painel de inventário de
instância, §34.74.8 — reuso deliberado, sem mensagem nova) e mostra
"Respawn na base em Xs" em vez do botão "Liberar espírito" — a contagem
usa `self._death_timer`, já acumulado localmente desde a morte, mesma
base de tempo que o servidor usa, sem precisar sincronizar nada novo.
Edge case coberto: sair via `/testbg leave` com o timer ainda contando
limpa `_state["respawn_timers"]` (senão tentaria `snap_to_tile` de volta
pro battleground depois do jogador já estar em outro mapa).

Testes: `tests/test_debug_battleground_respawn.py` (5 testes: inicia
timer, respawna time A, respawna time B, não respawna antes da hora,
sair morto limpa timer pendente — prova diferencial no caso principal).

#### §34.74.12 — Sexto round de playtest: causa raiz real de 4 bugs (compra, HP, talentos, ouro duplicado) + remoção do painel de 6 slots (02/08/2026)

Round de feedback mais profundo depois do §34.74.11 — usuário reportou que
os problemas continuavam mesmo após aquele fix. Investigação achou causas
raiz DIFERENTES e mais específicas pra cada um:

1. **Compra na loja de instância ainda não funcionava** — o fix do
   §34.74.11 (SAVE_STATE imediato após `inv_snapshot`) estava correto, mas
   o usuário testou antes do deploy pegar efeito. Nenhuma mudança nova
   aqui — reconfirmado pelos testes de `tests/test_client_instance_sync.py`.

2. **HP máximo não sincronizava (mostrava o valor REAL, ex. 390, em vez
   do piso de level 1)** — causa raiz: `PermanentStats` (bônus permanente
   de uma mecânica roguelike de morte, JÁ DESATIVADA — nada incrementa
   mais) nunca era trocado por um zerado durante a instância.
   `apply_char_stats_to_combat` (chamada por `_apply_talent_modifiers`
   dentro de `enter_/exit_normalized_progression`) SEMPRE soma
   `PermanentStats` em cima de `CharacterStats` — para um personagem
   estabelecido com bônus LEGADO (salvo de antes da mecânica ser
   desativada), isso vazava pro cálculo "normalizado". Fix:
   `InstanceProgressionSnapshot` ganhou `real_permanent_stats` — mesmo
   swap-e-restaura já aplicado a Wallet/Inventory/Equipment/TalentTree/
   PlayerSkills, agora também a `PermanentStats` (zerado durante a
   instância, restaurado exatamente ao sair). Verificado empiricamente:
   personagem com `perm.vitality=25` tinha `max_hp=620` real → `140` ao
   entrar (piso de level 1 puro) → `620` de novo ao sair. Testes:
   `tests/test_instance_progression.py::TestInstanceProgressionPermanentStatsSwap`.

3. **Pontos de talento pareciam acumular mesmo sendo usados** (18 pontos
   no level 6 da instância, deveria ser bem menos) — causa raiz: gap JÁ
   DOCUMENTADO em `_push_stats_update` ("alocação de talento completa
   NÃO coberto aqui") nunca tinha sido fechado. O `TalentTree` LOCAL do
   cliente nunca era trocado/resetado ao entrar na instância — só o
   `available_points` (escalar) sincronizava via `talent_points`; o
   `allocated{}` (dict) continuava sendo a alocação REAL, pré-instância,
   enquanto o SERVIDOR validava contra a árvore da instância (vazia).
   Cliente parecia "ganhar pontos de volta" porque `ui/talent_system.py::
   apply_talent_effects()` reembolsa tudo quando `chosen_build` diverge
   do esperado — mas a raiz mesmo era o desalinhamento de árvores
   inteiras, não um bug nesse reembolso específico. Fix: `talent_allocated`
   novo no payload de STATS_UPDATE (mesmo padrão de `inv_snapshot`),
   substituição COMPLETA (não merge) do `TalentTree.allocated` local.
   Testes: `tests/test_instance_progression.py::
   TestInstanceProgressionTalentSync`, `tests/test_client_instance_sync.py::
   TestInstanceTalentAllocatedSync`.

4. **Painel dedicado de 6 slots (§34.74.8) removido** — pedido do
   usuário, reversão explícita: "a ideia era os itens já valerem como
   equipados no momento que eu compro... pode remover os 6 slots da UI
   pois não está servindo de nada". `client/instance_inventory_panel.py`
   deletado, mixin removido de `game.py`; `ui.ui_components.
   InstanceInventoryUIState` MANTIDO (reaproveitado como flag genérica
   "estou no battleground de teste" — já usado pelo respawn automático do
   §34.74.11). Em troca: **compra de item equipável dentro da instância
   agora equipa direto** — `client/network_handlers.py::
   _handle_msg_buy_result` chama `self._equip_item(item)` logo após
   adicionar à bag, se `InstanceInventoryUIState.active` e o item tiver
   `slot` válido; `_equip_item` já valida classe/level (mesma lógica de
   sempre) — se rejeitar, o item simplesmente fica na bag (fallback
   gracioso, igual compra fora da instância). Testes:
   `tests/test_client_instance_sync.py::TestInstanceShopBuyAutoEquip`.

5. **Ouro de torre duplicava dentro da instância** — usuário reportou "só
   ganhei ouro lootando, não na hora da morte"; investigação achou o
   OPOSTO como bug real: `server_death_handler.py` concede ouro de
   instância instantâneo (§34.74.6, `grant_instance_gold`) E TAMBÉM
   sempre colocava `coins` no corpse da torre (loot normal — Torre nunca
   tinha essa parte suprimida, diferente de Minion, que já zera coins de
   propósito). O killer podia ganhar o MESMO ouro duas vezes; a percepção
   "só funciona lootando" provavelmente veio do ouro instantâneo passar
   despercebido (sem feedback visual — o HUD só atualiza o número, sem
   floating text) enquanto lootear é bem mais óbvio. Fix: `coins` do
   corpse vira 0 quando `grant_instance_gold` já rodou pra esse kill
   (flag `_instance_gold_ja_concedido`, calculada no mesmo bloco). Testes:
   `tests/test_towers.py::test_ouro_de_instancia_nao_duplica_com_coins_do_corpse`.

6. **Gap adicional achado e corrigido de propósito (não reportado
   diretamente, mas mesma classe de bug)**: recompensa de XP de QUEST
   (`server/session.py::_handle_quest_turn_in`) nunca checava
   `is_in_normalized_progression` antes de chamar `process_levelups`
   REAL — mesmo desvio que XP de kill de mob/minion/torre já tinha
   (`world_server.py::consume_xp()`), mas nunca estendido pra quest. Fix:
   mesmo padrão, desvia pra `grant_instance_xp` quando o player está em
   progressão normalizada. Teste: `tests/test_quest_turn_in.py::
   TestQuestTurnInInstanceXp` (XP de 10 milhões cai exatamente no cap 15
   da instância, não na curva real).

Todos os 6 itens acima têm prova diferencial (revertido, confirmado que
falha, restaurado). Suíte completa 3x limpa (723 testes, só as 2 falhas
pré-existentes conhecidas, sem relação com este trabalho).

#### §34.74.13 — Barra de ações e barra de consumíveis vazavam conteúdo REAL dentro da instância (02/08/2026)

Sétimo round de playtest: usuário reportou que a barra de ações (hotbar de
skills) continuava mostrando TODAS as skills do personagem real, e a
barra de consumíveis continuava mostrando os consumíveis reais — ambas
deveriam começar vazias na instância e se preencher sozinhas (skills
conforme sobe de level; consumíveis quando comprados no vendedor).

**Skills (hotbar)** — mesma classe de gap já fechada pra Inventory/
Equipment/TalentTree (§34.74.8/.12): `PlayerSkills` do CLIENTE nunca era
trocado/sincronizado ao entrar/sair da instância — o servidor já swapava
sua própria cópia corretamente (`enter_normalized_progression`), mas o
cliente nunca ficava sabendo. Fix: `skills_hotbar` (lista ordenada de
`skill_id`, um por slot da hotbar) + `learned_skill_ids` novos no payload
de STATS_UPDATE (`server/instance_progression.py::_push_stats_update`,
novo parâmetro `ps`), emitidos nos 3 pontos que já tocam `PlayerSkills`
(entrada, saída, e cada level-up de instância via
`_process_instance_levelup` — sem isso, a skill nova desbloqueada num
level-up nunca apareceria na hotbar). Diferente de item/equipamento,
skill é dado ESTÁTICO compartilhado (`content/skill_config.py::
SKILL_CATALOG`, cliente e servidor têm os dois) — só precisa mandar os
IDs; cliente reconstrói via `PlayerSkills._make_skill(skill_id,
SKILL_CATALOG)`, sem payload rico. Testes:
`tests/test_instance_progression.py::
TestInstanceProgressionSkillsHotbarSync`, `tests/test_client_instance_sync.py::
TestInstanceSkillsHotbarClientApply`.

**Consumíveis** — causa diferente: `ConsumableBar` (`engine/components.py`)
é 100% CLIENT-LOCAL — nunca existiu no servidor, é config pessoal do
jogador (nomes de item por slot, persistida em `config.json`). Não dá
pra seguir o padrão de swap server-side aqui. Fix: o próprio cliente
(`client/network_handlers.py::_handle_msg_stats_update`) faz o
snapshot/limpeza/restauração, gatilhado pela MESMA transição do campo
`in_instance` que já troca os outros componentes — guarda os slots reais
em `self._real_consumable_bar_slots` ao entrar (limpando a barra pra
`[None]*5`), restaura ao sair. **Auto-preenchimento ao comprar**: pedido
explícito do usuário — comprar um consumível na loja de instância agora
adiciona o nome do item automaticamente no primeiro slot livre da barra
(`_handle_msg_buy_result`, gate `item_type == "consumable"` +
`InstanceInventoryUIState.active`; não duplica se o nome já estiver em
algum slot). Testes: `tests/test_client_instance_sync.py::
TestInstanceConsumableBarSwap`, `TestInstanceBuyConsumableAutoAddsToBar`.

Todos os itens têm prova diferencial. Suíte completa 3x limpa (733
testes, só as 2 falhas pré-existentes conhecidas).

#### §34.74.14 — Nexus (fim de partida), placar final e HUD ao vivo de Kills/Deaths/Farm/Gold (02/08/2026)

Oitavo round de playtest: pedido do usuário pra fechar o "loop" de uma
partida MOBA de verdade — derrubar a torre principal (base) de um time
termina a partida na hora, com placar final dos dois times e um HUD
permanente durante a partida mostrando `Kills: N | Deaths: N | Farm: N |
Gold: N`. Reaproveita ao MÁXIMO a FORMA de 2 fases que a Arena já usa
(`server/match_processor.py::_finish_match`/`_arena_leave_now`) sem
nenhum acoplamento de código — `debug_battleground.py` continua "zero
acoplamento com Arena".

**`Tower.is_nexus`** (`engine/components.py`, repassado por
`entity_factory.create_tower`/`map_loader.py`/`world_server.py::
_create_towers`) — flag por-torre; as 2 torres-base de
`maps/moba_battleground_entities.json` (`(2,97)` time A / `(97,2)` time
B) têm `"is_nexus": true`. `server/server_death_handler.py`, no bloco de
morte de Torre, lê a `Faction` da torre MORTA (antes dela ser removida)
e chama `debug_battleground.notify_nexus_destroyed(world_server,
faction_da_torre, killer_eid)` só se `is_nexus` — no-op total se não
houver partida de teste carregada com esse time (Arena de verdade nunca
seta `is_nexus`, então nunca aciona nada aqui).

**Estatísticas por delta, não contador novo por-partida** — `dano`,
`kills` e `mortes` já existiam como campos VITALÍCIOS de
`CharStatsTracker` (incrementados no único chokepoint de dano/morte de
player, `_damage_tracker_composite`/`RespawnMixin._handle_player_death`)
— faltavam só 2 campos vitalícios novos: `CharStatsTracker.deaths` (
qualquer morte de verdade, qualquer contexto) e `CharStatsTracker.
minions_killed` ("farm", convenção MOBA — crédito do GOLPE FINAL/
`killer_eid`, diferente de `mobs_killed` que credita `first_attacker_eid`
= dono do loot/quest). `debug_battleground._enter()` tira um SNAPSHOT
desses 4 campos (`kills/deaths/farm/damage`) no momento do `/testbg a|b`
— qualquer estatística "desta partida" daí em diante é sempre
`valor_atual - snapshot`, sem precisar inventar um contador zerável.
Gold não precisa de snapshot: já é `wallet.gold - INSTANCE_STARTING_GOLD`
(a Wallet já foi resetada por `enter_normalized_progression`).

**Fim de partida — 2 fases, mesma forma da Arena**:
`notify_nexus_destroyed` (fase "decidida") monta o placar completo dos
DOIS times (confirmado com o usuário via pergunta — tabela cheia, não só
o próprio player) e popula `_state["pending_match_result"]` — NÃO
teleporta ninguém ainda. `_tick_bg_results_timeout` (fase "terminada",
chamada 1x por tick) força `_leave()` (já teleporta) de quem não saiu
manualmente em `DEBUG_BG_RESULT_AUTO_LEAVE_S = 15.0` segundos (mesmo
valor da Arena, constante LOCAL — não importa `ARENA_RESULT_AUTO_LEAVE_S`
de propósito). Botão "Voltar" do cliente sai ANTES do timeout mandando
`CHAT_SEND {"text": "/testbg leave"}` — reaproveita o comando de chat já
100% funcional, zero protocolo novo pra esse caminho.

**Mensagem nova**: `MsgType.BG_MATCH_RESULT` (S→C, `shared/messages.py`)
— `{"winner_faction", "players": [{eid,name,team,kills,deaths,farm,gold,
damage,won}, ...]}`. Nenhuma outra mensagem nova — a saída forçada por
timeout reaproveita `ZONE_CHANGE` normal. `server/session.py` drena os 2
buffers novos (`drain_match_result_notifications`/
`drain_forced_leave_notify`, mesmo padrão de
`drain_gate_open_notifications`) 1x por tick — **`has_pending`
estendido com os 2 buffers na mesma linha** (classe de bug já
documentada 2x neste arquivo, §34.34.1 e o par grupo/duelo/trade —
qualquer buffer novo esquecido aqui fica preso até atividade alheia
destravar o dispatch).

**HUD ao vivo** — `_tick_kda_hud` (1x por tick) calcula os 4 deltas e só
enfileira `STATS_UPDATE` (`ws.queue_stats_update`, canal já 100%
conectado — zero buffer novo) quando algum valor muda de verdade
(dirty-check, cache em `_state["last_kda_sent"]`) — mesmo espírito de
`WorldServer._sync_player_hp_dirty`. Cliente aplica os 4 campos
(`match_kills/match_deaths/match_farm/match_gold`) em
`InstanceInventoryUIState` (mesmo componente reaproveitado como flag
"estou na BG" desde §34.74.8 — reset pra 0 na transição `active` False→
True) via `client/network_handlers.py::_handle_msg_stats_update`, e
`client/hud_handlers.py::_draw_bg_kda_hud` desenha
`"Kills: N | Deaths: N | Farm: N | Gold: N"` no topo-centro, gated em
`InstanceInventoryUIState.active`.

**Cliente — novo mixin `client/battleground_handlers.py`**
(`BattlegroundHandlers`, adicionado à composição de `GameEngine` em
`game.py`) — modal de resultado (`_draw_bg_result_modal`, MESMO layout
visual do modal de fim de partida da Arena mas com colunas extras
K/D/Farm/Gold/Dano e os dois times agrupados, vencedor primeiro),
identificação de "minha linha" por `eid` (nunca por nome — mesmo
racional de §34.20/nameplate remoto, nomes colidem entre contas).
`_handle_msg_bg_match_result` abre o modal; `_handle_msg_zone_change`
(`client/network_handlers.py`) fecha (`self._bg_result_val = None`) —
ZONE_CHANGE é o ÚNICO sinal de "saí da BG" nesse design, tanto pra saída
manual quanto pro timeout forçado.

**Testes**: `tests/test_towers.py::TestNexusTowerEndsMatch` (torre
`is_nexus` derrubada dispara `notify_nexus_destroyed` corretamente; torre
comum não dispara — prova negativa), `tests/
test_debug_battleground_match.py` (placar reflete delta certo,
idempotência, no-op sem partida ativa, timeout força saída, dirty-check
do HUD só emite quando muda), `tests/test_session.py::
TestBattlegroundDispatchSemMovimento` (BG_MATCH_RESULT/ZONE_CHANGE
forçado chegam no MESMO tick sem depender de atividade alheia — mesmo
padrão de `TestArenaDispatchSemMovimento`/`TestPartyDispatchSemMovimento`),
`tests/test_char_stats.py::TestPlayerDeathTracking`/
`TestMinionFarmTracking`. Todos os hooks novos com prova diferencial.
Validado manualmente pelo usuário em jogo antes da suíte completa (ver
memória de convenção de projeto). Suíte completa 3x limpa (só as 2
falhas pré-existentes conhecidas).

#### §34.74.15 — INCIDENTE CRÍTICO: personagem real corrompido pelo battleground de teste (02/08/2026)

**Sintoma relatado pelo usuário**: personagem "jungo" (Arqueiro) com
talentos e skills reais já salvos apareceu "totalmente desconfigurado"
depois de usar `/testbg`. Investigação profunda (pedida explicitamente
pelo usuário, incluindo auditoria de todo `server/session.py`) achou a
causa raiz real — muito mais grave que qualquer bug de UI: **o banco de
dados estava sendo sobrescrito com o estado RESETADO da instância como
se fosse o personagem real**, permanentemente.

**Causa raiz**: `is_in_normalized_progression` (guard que evita persistir
enquanto o player está dentro da instância) só era checado em **1 dos 6**
pontos do código que gravam no banco (`on_disconnect`, que já chamava
`exit_normalized_progression` antes de salvar — ver §34.74.1/.2). Os
outros 5 pontos — todos pré-existentes, escritos MUITO antes do sistema
de instância existir, e nunca atualizados quando ele foi introduzido —
nunca verificavam:
1. **`_handle_save_state`** (SAVE_STATE) — o mais grave: o CLIENTE manda
   SAVE_STATE automaticamente a cada troca de mapa (`game.py::
   _do_transition`) E logo depois de qualquer `inv_snapshot`/
   `equip_snapshot` no STATS_UPDATE (`client/network_handlers.py`,
   fix de §34.74.11) — ou seja, o cliente literalmente dispara um
   SAVE_STATE sozinho ao ENTRAR e ao SAIR do battleground. Cada um
   desses persistia o overlay da instância (level 1, talentos/skills/
   inventário/gold resetados) direto no banco.
2. **`_handle_talent_update`** (TALENT_UPDATE).
3. **Entrega de quest** (`_handle_quest_turn_in`).
4. **`_autosave_all`** (autosave periódico, a cada 5 minutos — qualquer
   sessão conectada, sem exceção).
5. O kill-XP save de `world_server.py` (~linha 4138) já tinha o guard
   certo desde a implementação original de progressão normalizada
   (`continue` no branch `is_in_normalized_progression`) — mesmo padrão
   que devia ter sido generalizado, mas ficou isolado. Unificado também
   (mesmo dia, pedido do usuário) pra passar pelo MESMO chokepoint —
   `_persist_character` ganhou um parâmetro opcional `patch_fn` (aplica
   um patch no dict `merged` logo antes de salvar) só pra preservar o
   comportamento especial que esse call site já tinha (sobrescrever
   talentos com o `TalentTree` AO VIVO do ECS, porque
   `last_client_payload` pode estar com `available_points` desatualizado
   bem no instante de um level-up). Sem isso ainda sobraria 1 save fora
   do chokepoint único — seguro hoje (guard próprio), mas um lugar a mais
   pra alguém remover o guard sem perceber no futuro.

Ou seja: bastava um `/testbg a` seguido de QUALQUER um desses 4 gatilhos
(o mais comum: o próprio SAVE_STATE automático da troca de mapa pro
battleground) pra sobrescrever level/talentos/skills/inventário/gold
reais no banco — sem precisar de crash nem disconnect. Prova diferencial
capturou o payload exato que seria persistido: `{'stats': {'level': 1,
..., 'gold': 150}, ...}` — o piso da instância, gravado como se fosse a
conta real.

**Fix — chokepoint único**: `SessionManager._persist_character(session,
context)` (`server/session.py`) — TODO ponto que grava no banco agora
passa por aqui (nunca mais monta `get_player_save_data`/
`_build_save_merge`/`save_character` na mão). Retorna `None` sem tocar o
banco se `is_in_normalized_progression` for True. Os 5 pontos acima
foram refatorados pra usar esse helper — mesma filosofia de "único ponto
de verdade" já documentada em CLAUDE.md pra outras classes de bug deste
projeto (`has_pending`, `apply_damage_core`, etc.): a causa raiz do
incidente não foi "esquecer 1 lugar", foi **não existir 1 lugar só pra
esquecer** — a introdução do sistema de instância adicionou um invariante
novo ("nunca persistir dentro dela") sem migrar os pontos de persistência
JÁ EXISTENTES pra reconhecê-lo. Esse é o padrão a seguir daqui pra frente
pra qualquer feature nova que precise de estado "sandboxed"/descartável:
o guard entra no ÚNICO ponto que grava, nunca em cada call site.

**Pesquisa externa** (pedida pelo usuário): o padrão confirma a
abordagem — dar a todo dado efêmero/instanciado um identificador de
contexto claro (aqui, `InstanceProgressionSnapshot`/
`is_in_normalized_progression`) e tratar escrita "server-autoritativa"
como single source of truth que SEMPRE checa esse contexto antes de
gravar, em vez de confiar que cada gatilho de save individual vai
lembrar de checar sozinho.

**Testes**: `tests/test_instance_persist_guard.py` — prova que
`_persist_character` (chamado direto e através de `_autosave_all`/
`_handle_save_state`/`_handle_talent_update`) nunca chama
`save_character` (mockado) enquanto o player está na instância, e volta
a salvar normalmente depois que ele sai; `patch_fn` aplica quando persiste
fora da instância e nunca roda quando o guard barra; kill-XP real
(`create_enemy` + `PendingDeath` + tick, mesmo padrão de
`tests/test_towers.py`) aciona `_persist_character(context="kill_xp",
patch_fn=...)` de verdade. Prova diferencial: guard revertido →
`save_character` capturado sendo chamado com o payload `level=1/gold=150`
da instância → guard restaurado → todos passam. Chamada do kill-XP
removida → teste de wiring falha → restaurada → passa.

**Correções relacionadas, mesmo round de feedback do usuário** (todas
com prova diferencial):
- Talentos de instância agora vêm PRÉ-ALOCADOS no máximo desde
  `enter_normalized_progression` (não existe mais "gerenciar pontos"
  dentro da instância) — elimina de vez a classe de bug de
  `_apply_talent_modifiers` nunca sendo reaplicado quando uma skill
  talento-gated era concedida por level-up (stat do talento — ex.: HP —
  nunca entrava em CombatStats). `INSTANCE_TALENT_POINTS_PER_LEVEL`
  removida (não sobra ponto pra dar).
- HP agora cura 100% ao entrar, a cada level-up de instância, e ao sair
  — antes só `max_hp` mudava (o real, ex. 300/340, era só CLAMPADO
  contra o novo teto, nunca curado; level-up dentro da BG só subia
  `max_hp` sem tocar `current_hp`, diferente do jogo real
  — `engine/stats_system.py::process_levelups` já cura ao subir de
  nível há muito tempo).
- `PlayerAutoMove` (client/network_handlers.py::
  _handle_msg_player_revive) nunca era limpo ao reviver — se o player
  estava perseguindo/seguindo/andando até um alvo no chão quando morreu,
  o path/ground_target ANTIGO (perto de onde morreu) sobrevivia ao
  teleporte de revive (base do time, no caso da BG) e retomava sozinho
  assim que o movimento voltava a ser permitido — sintoma relatado:
  "anda sozinho até o ponto onde morreu". Mesmo padrão de limpeza já
  usado ao aplicar CC (root/stun/polymorph/disoriented).

Testes: `tests/test_instance_progression.py::TestInstanceProgressionTalentAutoMax/HpHeal`,
`tests/test_client_player_revive.py`. Suíte completa 3x limpa.

#### §34.74.16 — Sexta rodada de playtest: prioridade de clique, corpse de minion, Reciclagem e aggro de torre (03/08/2026)

Round de feedback com 6 itens; 4 corrigidos, 1 investigado sem regressão
real encontrada, 1 pendente de reprodução mais precisa do usuário.

**1. Prioridade de clique — alvo sempre em cima de loot no mesmo tile.**
Bug real: clique direito num inimigo que estava no MESMO tile de um
cadáver com loot fazia o personagem CAMINHAR PRO LOOT em vez de atacar,
e nem selecionava o alvo. Causa raiz: `LootSystem.update()`
(`ui/systems.py`) e `MouseTargetingSystem.update()` são dois `System`s
INDEPENDENTES que processam o MESMO evento de clique — `LootSystem.
_try_open_corpse` rodava incondicionalmente (sem checar se havia um
alvo vivo ali) e até CANCELAVA `CombatState.target_entity_id` que
`MouseTargetingSystem` já tinha setado no mesmo clique. Fix: novo helper
`_live_target_at_world_pos()` (mesma bounding-box de `_enemy_at_world_pos`/
`_remote_player_at_world_pos`, duplicada de propósito — Systems
independentes, sem referência um ao outro) — `_try_open_corpse` retorna
cedo se houver um alvo vivo (NPC/Player/Mob) na posição clicada. Teste:
`tests/test_client_ui.py::test_try_open_corpse_nao_compete_com_alvo_vivo_no_mesmo_tile`.

**2. Torre "dropando gold" — investigado, SEM regressão real
encontrada.** Escrevi um teste novo de kill RANGED de torre (Arqueiro,
cobertura que só existia pra melee) dentro da instância — passou sem
nenhuma mudança de código, confirmando que `_server_apply_ranged_physical`
já seta `PendingDeath` com o killer certo (spell_completion_processor.py,
fim da função) e a supressão de coins do corpse já funciona pra ranged
igual a melee. Usuário não conseguiu detalhar como matou a torre —
mantido em aberto; se reaparecer, é outro caminho de kill (skill
específica, DoT, multi-hit) não coberto ainda. Teste:
`tests/test_towers.py::test_ranged_kill_de_torre_nao_droppa_gold_no_corpse_dentro_da_instancia`.

**3. Corpse de minion — timer curto (4s), não mais 120s.** Minion nunca
tem loot (items/coins sempre vazios, por design — "moeda fora de
escopo") — usar o MESMO timer de mob normal (120s, pensado pra dar
tempo de lootear) só acumulava corpses vazios sem função nenhuma, e o
volume de mortes numa lane MOBA "floodava" o mapa rápido. Confirmado
com o usuário: ~4s, só confirmação visual de morte (estilo LoL/Dota).
`LootProcessorMixin.MINION_CORPSE_TIMER_S` (`server/loot_processor.py`)
— `server_death_handler.py` marca `"is_minion"` no `pending_loot.append`,
`_process_loot_drops` escolhe o timer por essa flag. Teste:
`tests/test_towers.py::test_corpse_de_minion_usa_timer_curto_nao_o_de_mob_normal`.

**4. Reciclagem dentro da instância vai direto pra bag.** Confirmado com
o usuário: flecha recuperada (talento Reciclagem) não deveria mais
precisar que o player lootasse o corpse manualmente dentro da BG — vai
direto pro `Inventory` do killer (empilha se já tiver o mesmo tipo de
munição; cai no fallback antigo — corpse — se a bag estiver cheia).
Fora da instância, comportamento 100% inalterado. `server/
server_death_handler.py`, bloco "5b. Reciclagem". Testes: `tests/
test_instance_progression.py::TestInstanceReciclagemGoesToBag`.

**5. Aggro por dano de torre + espalhamento em área pra minions.**
Pedido do usuário: "quando a torre ataca um minion, ele agra na torre e
os minions em torno também agrem". `MinionSystem._check_tower_aggro`
(`engine/world_systems.py`) — reaproveita a MESMA lista `combat_this_tick`
que já alimenta o aggro-switch de `TowerSystem` (nenhum mecanismo
novo): torre que dana um minion vira alvo IMEDIATO dele (override do
estado atual); minions ALIADOS (mesma facção) do atingido, dentro de
`TOWER_AGGRO_SPREAD_RADIUS_TILES` (6 tiles, valor tunável — sem número
exato pedido pelo usuário) e ainda `ADVANCING` (não puxa quem já está
brigando com outra coisa — evita abandonar duelo por dano de raspão num
aliado distante), também trocam pra torre. `WorldServer._tick` agora
passa `combat_this_tick` pro `MinionSystem.update()` também (antes só
`TowerSystem` recebia). Bug lateral achado ao testar: `_target_still_valid`
usava só `minion.aggro_range_tiles` (raio PASSIVO de detecção) — uma
torre que acabou de acertar o minion de LONGE (attack_range de torre
tipicamente bem maior que aggro_range passivo de minion) invalidava o
alvo forçado no MESMO tick em que era setado. Fix: quando o alvo é uma
Torre, usa `max(minion.aggro_range_tiles, tower.attack_range_tiles)`.
Testes: `tests/test_minions.py::TestMinionTowerAggro` (5 casos: aggro
direto, espalhamento em área, fora do raio não agra, minion do mesmo
time da torre não agra, minion já engajado não é puxado).

**6. HP máximo "puxava" o valor real ao recalcular (ex.: ao comprar um
item) — RESOLVIDO.** Reprodução mais precisa do usuário: "entrei na BG,
HP 140/140 — comprei um item e o HP ficou 140/380" (380 = max_hp REAL,
fora da instância). Causa raiz real (bem mais profunda que um race de
rede): `CombatStats.max_hp`/`base_stamina` no CLIENTE são recalculados
DO ZERO (`stat_fns.recalculate_combat_stats`, chamado por QUALQUER
`add_modifier`/`remove_modifier`) a partir de `CharacterStats` +
`PermanentStats`, via `apply_char_stats_to_combat`. `_push_stats_update`
(server/instance_progression.py) só mandava o RESULTADO (`hp`/`hp_max`)
pro cliente, nunca os atributos brutos (`strength/intelligence/agility/
vitality/defense`) que ALIMENTAM esse recálculo — o `CharacterStats`
LOCAL nunca sabia que tinha entrado na instância, guardava a vitalidade
REAL pra sempre. Qualquer gatilho de recálculo local (equipar um item
comprado — `client/inventory_handlers.py::_equip_item`, que chama
`add_modifier` por cada modifier do item — é um desses gatilhos)
recomeçava do `base_stamina` ERRADO (derivado da vitalidade real) e
trazia o max_hp real de volta. Mesma classe de bug do `talent_allocated`/
`skills_hotbar` já corrigidos nesta sessão, só que pros 5 atributos
brutos, e mais perigosa: TODOS os outros `base_*` (armor, attack_power,
crit, dodge, parry, acerto, hp5/mp5...) tinham o MESMO problema —
qualquer stat de combate, não só HP, ficava incorreto no cliente após
qualquer equip/buff dentro da instância (quebra de propósito do sistema
inteiro: "todo mundo normalizado pro mesmo piso").

Fix: `_push_stats_update` agora sempre inclui os 5 atributos brutos no
payload de STATS_UPDATE (enter/exit/level-up de instância, os 3 pontos
que já chamam essa função). Cliente (`_handle_msg_stats_update`) aplica
em `CharacterStats` e re-roda `apply_char_stats_to_combat` +
`sync_attack_interval` localmente — MESMA função pura que o servidor
usa, então qualquer recálculo futuro (equip/unequip) já parte da
baseline certa. `hp`/`hp_max` explícitos continuam tendo a palavra final
(aplicados depois, no mesmo payload). Teste (prova diferencial, incluindo
um falso positivo pego e corrigido no caminho — item sem `modifiers`
não dispara `add_modifier`/recálculo nenhum, mascarando a prova):
`tests/test_client_instance_sync.py::TestInstanceAttributeSyncSurvivesRecompute`.

#### §34.74.17 — Follow-up do item 6: PermanentStats vazando + HP não acompanhava equip (03/08/2026, mesmo dia)

Usuário testou o fix do item 6 e achou 2 problemas novos no mesmo
mecanismo, ambos com o Arqueiro "jungo":

**1. "Comprei o Arco do Caçador e o HP foi pra 160, mas o item não dá
atributo de vida nenhum" (o item só tem `crit_rating`).** Causa raiz:
`PermanentStats` (bônus legado da mecânica roguelike desativada — ver
docstring de `InstanceProgressionSnapshot`) é zerado pelo SERVIDOR ao
entrar na instância (`ws.world.add_component(eid, PermanentStats())`
fresh), mas a cópia LOCAL do cliente nunca era avisada disso. O fix do
item 6 (§34.74.16) chama `apply_char_stats_to_combat(char, cs, perm)`
localmente — usando o `PermanentStats` LOCAL, que pra um personagem
ESTABELECIDO com bônus legado real (>0 — "jungo" é um desses) somava
esse bônus em cima do atributo já normalizado, toda vez que qualquer
equip disparava o recálculo. Fix: `InstanceInventoryUIState.active`
(mesma flag "estou na BG") decide se o recálculo usa `PermanentStats`
real ou `None` (zero) — usa o `in_instance` do PRÓPRIO payload se vier
explícito (enter/exit sempre mandam), senão o estado atual da flag
(level-up de instância não manda `in_instance`, mas o player continua
lá dentro).

**2. "current_hp deve acompanhar o max_hp — se o personagem está 100%
de hp, equipar um item precisa continuar 100%, ele não sofreu dano
nenhum."** `stat_fns.recalculate_combat_stats` (compartilhado,
propositalmente) só preserva o HP ABSOLUTO ao recalcular (comportamento
padrão de buff/debuff em qualquer RPG — current_hp não muda, a %
cai se max_hp subir) — decisão de NÃO mexer nessa função compartilhada
(afetaria buff/equip no jogo INTEIRO, fora do escopo do pedido). Fix
escopado só no auto-equip da COMPRA de instância
(`client/network_handlers.py::_handle_msg_buy_result`): captura a
FRAÇÃO de HP antes de `_equip_item`, reaplica a mesma fração contra o
`max_hp` novo depois — comprar/equipar não é dano nem cura, é
normalização de equipamento.

Testes: `tests/test_client_instance_sync.py::
TestInstanceAttributeSyncSurvivesRecompute::
test_max_hp_nao_vaza_bonus_legado_de_permanent_stats_ao_recalcular`,
`TestInstanceShopBuyAutoEquip::test_auto_equip_preserva_fracao_de_hp_*`
(2 casos: 100% e fração parcial). Ambos com prova diferencial.

#### §34.74.18 — Remoção do `DeathRespawnSystem` (mecânica roguelike abandonada, 03/08/2026)

Usuário pediu pra investigar e eliminar código antigo da mecânica
"roguelike" (morrer acumula stats em `PermanentStats`, reseta
`CharacterStats`, respawna com HP cheio trocando de mapa) — ideia
abandonada quando o projeto migrou pro modelo online autoritativo.

Investigação confirmou `DeathRespawnSystem` (`engine/stats_system.py`)
100% inerte no branch online: `client/online_mode_handlers.py::
_connect_online()` seta `online_mode=True` incondicionalmente, e a
primeira linha de `DeathRespawnSystem.update()` era `if self.online_mode:
return`. Nenhum teste referenciava a classe. Morte de player online é
tratada inteiramente pelo servidor (`server/respawn_system.py`,
autoritativo — ghost/respawn já documentado na seção de Sistemas do
Servidor).

Removido:
- `engine/stats_system.py`: classe `DeathRespawnSystem` inteira + menção
  no docstring do módulo.
- `game.py`: import, instanciação, `self._death_respawn_system`, entrada
  na lista `self.systems`, entrada em `_SYSTEM_ORDER_CONSTRAINTS`, e o
  bloco de `pending_respawn` no loop principal.
- `client/online_mode_handlers.py`: `self._death_respawn_system.online_mode
  = True` em `_connect_online()`.
- `arquitetura/SISTEMAS_ECS.md`: linha da tabela de sistemas (renumerado
  20→28 do que vinha depois).
- Docstring de `_handle_death()` (`engine/world_systems.py`) corrigida
  (mencionava `DeathRespawnSystem` num comentário, não código morto —
  agora referencia o fluxo real de ghost/respawn do servidor).

Verificado com `python -m py_compile` em cada arquivo tocado + suíte
completa (`pytest tests/ -q`), sem regressão.

**Não removido, decisão do usuário:** `PermanentStats` (componente que a
mecânica escrevia) — hoje é só leitura legada, nada mais produz valores
novos nele, mas personagens ESTABELECIDOS (ex.: "jungo") podem ter
valores reais >0 salvos no banco. Perguntado ao usuário se queria
remover o componente inteiro também (apagaria esse bônus de personagens
existentes) — resposta: **deixar como está** (read-only, sem produtor,
zero risco a mais do que já existe hoje).

#### §34.74.19 — INCIDENTE CRÍTICO: bag perdida ao sair da BG (03/08/2026)

Usuário relatou: "estou perdendo os itens da bag quando saio da BG,
parece que o jogo está persistindo a bag vazia da instância para fora
da instância, isso é grave" — mesma classe de risco do incidente
§34.74.15 (corrupção de progressão real), desta vez no Inventory.

**Causa raiz** — race de ORDEM DE MENSAGENS entre dois canais de rede
diferentes: o ZONE_CHANGE de `/testbg leave` era mandado IMEDIATAMENTE
dentro de `SessionManager._handle_chat` (`await session.send(...)`,
síncrono, fora do tick), enquanto o STATS_UPDATE com o
Inventory/Equipment/CharacterStats REAIS — restaurados por
`exit_normalized_progression` via `WorldServer.queue_stats_update` — só
é despachado no PRÓXIMO tick (`_dispatch_tick_deltas` drena
`_pending_stats_updates`). O cliente recebia o ZONE_CHANGE primeiro,
processava `_do_transition` (game.py) — que dispara `self._autosave()`
no fim — com o Inventory LOCAL ainda "de instância" (vazio/6 slots),
porque o STATS_UPDATE corretivo ainda não tinha chegado. Como
`exit_normalized_progression` já tinha rodado no SERVIDOR antes do
ZONE_CHANGE ser enviado, `is_in_normalized_progression` já era False
quando esse SAVE_STATE prematuro chegava — o guard de `_persist_character`
(§34.74.15) não bloqueava mais, e a bag vazia era gravada no banco por
cima da real.

Confirmado que o caminho de saída FORÇADA por timeout (`_tick_bg_results_
timeout`) já era seguro por acidente — o ZONE_CHANGE dela passa pelo
MESMO `_dispatch_tick_deltas` que drena os STATS_UPDATE, na ordem certa
(stats antes de zone). Só o `/testbg leave` MANUAL (chat síncrono) tinha
o problema.

**Fix**: `SessionManager._flush_stats_updates()` (novo método, extrai a
lógica que já existia inline em `_dispatch_tick_deltas`) chamado
explicitamente ANTES do `await session.send(MsgType.ZONE_CHANGE, ...)`
no branch `/testbg` de `_handle_chat` — garante que a restauração real
sempre chega ao cliente antes da troca de mapa, nos dois sentidos
(entrar e sair). Mesma classe de bug já documentada neste projeto sob
"atualização coesa" (CLAUDE.md) — ordem de despacho de dois canais que
parecem independentes mas têm uma dependência causal implícita.

Teste: `tests/test_bg_leave_message_order.py` (novo arquivo) — login real
via `fake_login`, item real e distinto na bag antes de entrar, `/testbg
a` → `/testbg leave` via `mgr.on_message`, verifica ORDEM das mensagens
capturadas (`STATS_UPDATE` com `inv_snapshot` chega ANTES do
`ZONE_CHANGE`) e que a bag restaurada tem o item real. Prova diferencial
confirmada (revertido o flush antecipado → teste falha, `inv_snapshot`
nem chega a ser enviado antes do ZONE_CHANGE).

#### §34.74.20 — Torre ainda dropava ouro físico quando o golpe final era de um minion (03/08/2026)

Usuário relatou (com print, personagem "jungo" dentro do `/testbg`):
torre morta ainda dropava um coin físico no corpse, mesmo dentro da
progressão normalizada (onde o design é ouro SEMPRE automático — ver
§34.70/34.74.16). Investigação inicial (repro de auto-attack melee E
ranged reais, via `WorldServer._process_player_attacks`) não reproduziu
— ambos creditavam o ouro corretamente e suprimiam o coin do corpse.

**Causa raiz real**: `MinionSystem` também chama `deal_damage`
(`engine/world_systems.py:1806`) quando um minion aliado ataca uma
torre — se o GOLPE FINAL vier do minion (cenário normal de lane MOBA:
player + minions batendo juntos), `PendingDeath.killer_entity_id` fica
com o eid do MINION, não de um player. O gate antigo em
`server_death_handler.py` (`killer_eid in _player_eids_now`) falhava
por completo nesse caso: nem creditava ouro automático (killer não é
player) NEM suprimia os coins físicos do corpse
(`_instance_gold_ja_concedido` nunca virava `True`) — a torre voltava a
dropar ouro físico normal, exatamente como no print.

**Fix**: novo `_gold_recipient_eid` — usa `killer_eid` se ele for um
player de verdade; senão, cai pro PRIMEIRO player que bateu
(`damage_log`, já filtrado só-players nesta função, mesma fonte que
`first_attacker_eid`/dono do loot usa mais abaixo). Se NENHUM player
bateu (minion mata sozinho, sem nenhum player por perto), o fallback
fica `-1` e o comportamento antigo (coins físicos normais) se mantém —
correto, ninguém pra creditar.

Teste: `tests/test_towers.py::TestTowerDeathXpGoldRespawn::
test_torre_morta_por_golpe_final_de_minion_credita_ouro_ao_player` —
player bate na torre, MINION dá o golpe final (`PendingDeath(killer_
entity_id=minion_eid)` + `damage_log` com os dois), confirma que o ouro
vai pro player e o corpse fica com `coins=0`. Prova diferencial
confirmada (revertido o fallback → reproduz exatamente o bug do
usuário: `coins=20` no corpse, gold do player intocado).

#### §34.74.21 — HUD de Gold da BG ficava negativo ao comprar item (03/08/2026)

Usuário relatou: "compra de itens faz o contador de gold da HUD ficar
negativo, não é necessário descontar o gold usado". O HUD ao vivo
(§34.74.14) calculava `gold` como `wallet.gold - INSTANCE_STARTING_GOLD`
— um valor NET (líquido), que caía abaixo de 0 assim que o player
gastasse na loja de instância mais do que tinha ganho até ali. Pedido
explícito: gold da HUD/placar final deve ser só o GANHO (kill/loot),
gasto não desconta.

**Fix**: novo acumulador monotônico `_state["gold_earned"]`/
`_state["last_wallet_gold"]` (`server/debug_battleground.py`) — função
`_sample_gold_earned(ws, eid)` amostra `wallet.gold` 1x por tick e soma
SÓ deltas POSITIVOS no acumulado; um delta negativo (compra) só
atualiza o baseline (`last_wallet_gold`), nunca subtrai do total
mostrado. Inicializado em `_enter()` (zerado, baseline =
`INSTANCE_STARTING_GOLD`, já aplicado por `enter_normalized_
progression`), limpo em `_leave()`. Usado tanto por `_tick_kda_hud`
(HUD ao vivo) quanto por `notify_nexus_destroyed` (placar final,
reamostra na hora — o Nexus cai ANTES do gold do próprio kill ser
creditado no mesmo tick em `server_death_handler.py`, então sem essa
reamostra o placar final perderia esse último delta).

Teste: `tests/test_debug_battleground_match.py::
test_gold_ganho_nao_diminui_quando_player_gasta_na_loja` — ganha 100,
gasta 80 (delta negativo), confirma que o valor mostrado continua 100
(não cai pra 20); ganha mais 30, confirma 130. Prova diferencial
confirmada (removido o guard `if delta > 0` → reproduz exatamente o bug
do usuário: valor cai de 100 pra 20 após a compra).

Todos os itens 1-6 (+ follow-up) com prova diferencial. Suíte completa
3x limpa.

#### §34.74.22 — CORREÇÃO do diagnóstico §34.74.17: causa real do "HP 140→160 ao comprar item sem atributo de vida" (03/08/2026)

Usuário reportou de novo (com 2 prints das Estatísticas do personagem,
antes/depois de comprar o Arco do Caçador) exatamente o mesmo sintoma do
§34.74.17, mesmo após o fix daquele item e um restart real do servidor.
Investigação profunda (múltiplos repros com `SessionManager` real,
`BUY_REQUEST`/`BUY_RESULT` reais, e por fim os dados REAIS de "jungo"
lidos direto de `data/game.db`) não reproduziu via a teoria do
§34.74.17 — o que levou a uma descoberta importante:

**`PermanentStats` nunca é persistido no banco** — não existe coluna
`permanent_stats_json` (ou equivalente) na tabela `characters`
(confirmado via `PRAGMA table_info`). Toda entidade nasce com
`PermanentStats()` fresh (zerado) em `entity_factory.py`/`WorldServer.
spawn_player` — não há NENHUM mecanismo pra carregar um valor >0 do
banco. Ou seja: a premissa do §34.74.17 ("personagem estabelecido com
bônus legado real, jungo é um desses") está **errada** — esse bônus não
pode existir pra nenhum personagem persistido, então o gate de
`_perm_attr` daquele fix, embora inofensivo, não tinha como ser a causa
real (o vazamento sempre foi zero × qualquer coisa = zero).

**Causa raiz real, achada pelo print do usuário**: o painel de
Estatísticas mostrava "HP: 120 Base +20 Itens" e "Estamina: 140 Base +20
Itens" com a mochila de equipamento **totalmente vazia** — ANTES de
comprar qualquer coisa. Isso apontou pra `CombatStats.modifiers` (não
`PermanentStats`). `equip_snapshot` (mandado por `enter_/exit_
normalized_progression`, aplicado em `client/network_handlers.py::
_handle_msg_stats_update`) só trocava `Equipment.slots` — nunca limpava
os `Modifier(source="equipment")` correspondentes já presentes em
`CombatStats.modifiers`, adicionados no LOGIN por `_restore_save_state`
(`client/save_sync_handlers.py:354-365`, que existe desde sempre e
chama `add_modifier` pra cada item REAL equipado). `Equipment.slots` e
`CombatStats.modifiers` são listas **independentes** — esvaziar uma não
esvazia a outra. Pra "jungo" (armadura real de cabeça/peito/botas):
armor +9, crit_rating +2%, stamina +2 (=+20 HP, `_FLAT_SCALE["stamina"]
=10.0`) ficavam silenciosamente presos em `cs.modifiers`, mascarados
pelo `hp`/`hp_max` explícito do mesmo payload de entrada — só ficavam
visíveis no próximo `recalculate_effective_stats()` (disparado por
QUALQUER `add_modifier`/`remove_modifier`, ex.: equipar o Arco do
Caçador — que só tem `crit_rating`, nada de vida) reaplicar TUDO de
novo, incluindo os 3 modifiers reais órfãos.

**Fix**: `_handle_msg_stats_update`'s bloco de `equip_snapshot`
(`client/network_handlers.py`) agora espelha exatamente o que o
SERVIDOR já faz em `WorldServer._apply_equipment_modifiers` — limpa
`cs.modifiers` de tudo com `source="equipment"` e reconstrói do zero a
partir do `Equipment` que ACABOU de ser trocado (vazio ao entrar na
instância, real ao sair) — nunca deixa a lista de modifiers dessincronizar
do conteúdo real do Equipment.

Teste: `tests/test_client_instance_sync.py::
TestInstanceAttributeSyncSurvivesRecompute::
test_max_hp_nao_vaza_modifier_de_equipamento_real_ao_entrar_na_instancia`
— equipa um peito com modifier real de stamina, processa ENTRY com
`equip_snapshot` vazio, confirma `cs.modifiers` sem sobra de
`source="equipment"` e `max_hp` no piso certo; equipa um item só com
`crit_rating` e confirma que `max_hp` não muda. Prova diferencial
confirmada — sem o fix, reproduz o EXATO delta do usuário (140→160,
+20) usando os dados reais de "jungo".

#### §34.74.23 — Cooldown de skill não aparecia + CC (slow/sleep/stun/root) sem efeito em minion/torre, dentro da BG (03/08/2026)

Usuário relatou dois bugs no mesmo playtest, ambos só dentro do
`/testbg`. Investigado via agent de pesquisa dedicado + verificação
manual antes de mexer no código.

**Bug A — cooldown de skill só aparecia depois de apertar o atalho de
novo.** Causa raiz: `skills_hotbar` (payload de STATS_UPDATE) chega a
CADA level-up de instância — e level-up é muito frequente na BG (XP por
proximidade de qualquer minion morto, waves contínuas). `client/
network_handlers.py::_handle_msg_stats_update` reconstruía a barra
INTEIRA (`PlayerSkills._make_skill` pra cada slot) toda vez que esse
payload chegava — um `Skill` novo sempre nasce com `current_cooldown=0.0`,
`charges=0`, `charge_timer=0.0`. Um cooldown real que o SKILL_RESULT
tinha acabado de mandar segundos antes ficava visualmente limpo até a
PRÓXIMA mensagem (ex.: a rejeição de um 2º uso) reaplicar o valor certo
de novo — exatamente o sintoma relatado. Confirmado em log real do
servidor: CD registrado de 90s (`tiro_multiplo`), cliente mostrando
pronto ~30s depois.

Fix: reconciliação POR SLOT em vez de substituição completa —
`client/network_handlers.py` (bloco `skills_hotbar`) mantém o objeto
`Skill` JÁ EXISTENTE quando o `skill_id` do slot não mudou (preserva
`current_cooldown`/`charges`/`charge_timer`/`_server_pending`/
`fail_flash_timer` ao vivo); só cria um `Skill` novo pra slot que
realmente mudou de conteúdo (nova skill liberada, ou vazio↔ocupado).

Teste: `tests/test_client_instance_sync.py::
TestInstanceSkillsHotbarClientApply::
test_skill_no_mesmo_slot_preserva_cooldown_ao_vivo`. Prova diferencial
confirmada.

**Bug B — slow/sleep/stun/root sem efeito em minion.** Causa raiz:
`MinionSystem` não usa `AIControlled`/`EnemyAISystem` DE PROPÓSITO (ver
docstring da classe, `engine/world_systems.py`) — só que isso também
significa que nunca passava pelo bloco de CC (stun/sleep/fear/
polymorph/disoriented/root) que `EnemyAISystem` aplica pra mob normal.
`MinionSystem._attack` disparava incondicionalmente, sem checar
`StatusEffects` nenhuma. Investigação também confirmou que
**movimento** sob stun/sleep já tinha proteção pré-existente e
GENÉRICA em `TileMovementSystem.update()` (cancela `is_moving` de
qualquer entidade com esses 2 efeitos ativos, independente de quem
iniciou o passo) — o gap real e novo era especificamente ATACAR (e
mover sob **root**, que `TileMovementSystem` não cobre — só stun/sleep).

Fix: `MinionSystem.update` (`engine/world_systems.py`) ganha o mesmo
choke-point único já documentado em `engine/utils.py` —
`is_action_locked` (stun/sleep/fear/polymorph/disoriented — já usado
por `combat_processor.py`/`skill_processor.py`/`PlayerInputSystem`)
barra COMPLETAMENTE o tick (nem anda nem ataca); `is_movement_locked`
(mesmo conjunto + root) barra só o `_walk_toward`, deixando o ataque
acontecer se o alvo já estiver no alcance.

**Torre NÃO ganhou o mesmo gate — decisão do usuário (03/08/2026, ao
revisar o fix): "a torre não é um ser vivo, é uma estrutura, não deve
sofrer nenhum efeito só dano"**. `TowerSystem.update` foi tentado com o
mesmo `is_action_locked` no primeiro rascunho deste fix e foi
DESFEITO — torre continua imune a stun/sleep/fear/polymorph/
disoriented/root de propósito, só HP a afeta. Comentário explícito no
código pra não reintroduzir o gate sem confirmar de novo.

Testes: `tests/test_minions.py::TestMinionCrowdControl` — stun/sleep
bloqueiam ataque de minion, root bloqueia perseguição mas não ataque em
alcance (todos com prova diferencial); `test_torre_atordoada_continua_
atacando` confirma o OPOSTO pra torre (CC não impede o ataque —
estrutura, não "ser vivo").

**Não mexido, achados incidentais da investigação (fora do escopo dos
2 bugs relatados):** `_skill_impacto` (`ui/skill_handlers.py`) não
filtra `can_engage` — pode acertar aliados na BG, diferente de Brado
Provocativo/Canção de Ninar que já filtram; `sleep` quebra com QUALQUER
dano (`apply_damage_core`), tornando sleep em área quase inútil numa
troca de lane contínua — decisão de design, não bug, não mexido sem
perguntar; `taunted` não força alvo em minion/torre (não tem
`blocks_act`/`blocks_move`, é um mecanismo à parte de forçar
targeting, nunca implementado pra NPC/minion/torre).

#### §34.74.24 — Silencia FLT/som de combate sem player, dentro da BG estilo MOBA (03/08/2026)

Usuário relatou spam de dano/som de batalha entre minions na BG. Pedido:
dentro do `/testbg` (só lá — resto do jogo intocado), combate onde
NENHUM dos dois lados é um player de verdade (minion×minion,
minion×torre, torre×minion) fica sem número de dano flutuante e sem
som; qualquer lado sendo player mantém o feedback normal — inclusive
quando é o MINION que bate NO player (correção do usuário no mesmo
pedido, pra não silenciar esse caso por engano).

Todo combate entre duas entidades que o player só está VENDO (não as
suas próprias) chega por um único caminho — `combat[]` dentro de
`AOI_UPDATE` → `client/remote_entity_handlers.py::_apply_combat_result`
(mesma função usada pro combate do próprio player, `server_attacker`/
`server_target` genéricos). Novo helper `_bg_silence_nonplayer_combat_fx
(server_attacker, server_target)`: `False` fora da BG (`Instance
InventoryUIState.active`) ou se qualquer um dos dois lados resolve pra
player (`== self._my_eid` ou `in self._remote_players`); `True` só
quando os dois são não-player. Aplicado nos 2 lugares que geram FLT/som
de golpe entre entidades vistas: o branch "mob foi atacado" de
`_apply_combat_result` (crit, normal, e miss/dodge/parry/block/evade) e
o som de DISPARO de projétil de mob (`_spawn_mob_projectile` — atacante
aqui é sempre mob, só falta checar o alvo).

Teste: `tests/test_client_ui.py` — 4 casos (`bg_silencia_flt_e_som_
minion_vs_minion`, `bg_mantem_flt_e_som_player_vs_minion`, `bg_mantem_
flt_e_som_minion_vs_player`, `fora_da_bg_minion_vs_minion_continua_com_
flt_e_som`). Prova diferencial confirmada.

**Fora do escopo, achados incidentais não mexidos:** som de MORTE de
minion (`ENTITY_DESPAWN`/despawn em `AOI_UPDATE`) não tem informação de
quem matou disponível no payload — não dá pra distinguir "minion morto
por outro minion" (deveria silenciar) de "minion morto pelo player"
(deveria continuar, é o kill DO player) sem plumbing novo; som de
"aggro" de mob (`SOUND_EVENT` kind=`mob_aggro`) também não carrega
alvo, só o mob que aggrou — um gate por-BG aí silenciaria até um minion
aggrando NO player. Nenhum dos dois foi pedido explicitamente — avisar
se continuarem incomodando.

#### §34.74.25 — Lanes top/bot da BG nunca spawnavam minion (só mid funcionava) (03/08/2026)

Usuário só tinha testado a lane mid ("é reta, então é simples") e pediu
pra liberar as outras. As 6 lanes (top/mid/bot × 2 times) JÁ estavam
100% definidas em `maps/moba_battleground_entities.json` e JÁ eram
ativadas (`_activate_minion_lanes` não filtra por lane) — não era um
flag faltando, eram 3 bugs reais silenciosos:

**1. `max_nodes=300` (default do pathfinder,
`engine/world_systems.py::PathfindingSystem.find_path`) baixo demais
pras lanes com curva.** `server/world_server.py::_tick_minion_waves`
calcula 1 `find_path` por PERNA (spawn→waypoint→...→base). Mid é uma
reta direta (1 perna só, ~100-270 nós explorados); top/bot têm 2
pernas cada, contornando bordas do mapa 100×100 — cada perna precisa
de ~450-590 nós. A falha (`route_ok=False`) só dava `continue`, sem
NENHUM log — 0 minions, parecia "a lane não existe". Fix: `max_nodes
=4000` no `find_path` (`server/world_server.py`, dentro do loop de
`_tick_minion_waves`) + log de falha (`log.warning`, nunca mais
silencioso).

**2. `target_tile` da lane mid do time B caía EXATAMENTE no tile do
Nexus do time A** (`maps/moba_battleground_entities.json` — Nexus A em
`[4,95]` `is_nexus:true`, B-mid `target_tile` também `[4,95]`).
Torres entram como `dynamic_obstacles` do cálculo de rota
(`_get_tower_tiles_for_map`) — alvo inalcançável por definição,
independente do budget de busca (confirmado: mesma rota com a torre
removida resolve em 90 nós). O Nexus A foi movido de `(2,97)` pra
`(4,95)` em algum momento sem atualizar o alvo da lane B-mid junto.
Fix: `target_tile` de B-mid voltou pra `[2, 97]` (a posição antiga do
Nexus — walkable, sem colisão com nada, mesma ordem de grandeza de
distância que as outras pernas).

**3. 3 dos 7 `_MINION_WAVE_OFFSETS` caem em parede em lanes de base
"estreita"** (A-top offset `(-1,0)`→`(1,93)`; A-bot offsets `(0,1)`/
`(0,2)`→`(6,98)`/`(6,99)`) — minion nascia visualmente preso dentro de
uma parede (mid tem espaço de sobra ao redor do spawn, por isso nunca
pegava). Fix: `_tick_minion_waves` agora valida o tile do offset via
`tile_validation.is_tile_walkable` — se cair em parede, volta pro tile
PURO da lane (`spawn_tile`, sempre andável por construção) em vez de
nascer preso.

Todos os 3 só ficam visíveis no mapa REAL da BG (100×100) — os testes
existentes de `TestMinionWaveSpawning` usam distâncias sintéticas
curtas (mapa `map_1.csv`, poucos tiles), nunca cruzaram o limiar de
nenhum dos 3 bugs.

Teste: `tests/test_minions.py::TestMinionWaveSpawning::
test_todas_as_lanes_reais_da_bg_produzem_rota_valida` — carrega o mapa
E o `moba_battleground_entities.json` REAIS (não dado sintético), abre
os portões dos 2 times (mesma mutação de tile que `_tick_gate` faz de
verdade — sem isso as bases são ilhas isoladas de ~25 tiles, sem
conexão nenhuma com o resto do mapa), ativa as 6 lanes, roda 1 wave e
confirma: 42 minions no total (6 lanes × 7) e NENHUM nasceu dentro de
parede. Prova diferencial confirmada nos 3 bugs independentemente
(cada um revertido isoladamente reproduz o sintoma exato).

#### §34.74.26 — Freeze/reconexão ao spawnar todas as lanes juntas: pathfinding bloqueando o event loop (04/08/2026)

Depois do fix de §34.74.25 (todas as 6 lanes liberadas), usuário reportou em
playtest: minions travados, player se movendo mas "nada acontecendo", depois
minions tomando dano parado sem inimigo por perto, tela de "reconectando",
travamento de novo. Log do servidor confirmou a causa:
`ConnectionClosedError(Close(code=1011, reason='keepalive ping timeout'))`
repetido — o watchdog interno da lib `websockets` fechando a conexão porque
o event loop não respondeu a tempo a um ping. `WorldServer._tick` é `def`
síncrono, não `async def`: qualquer trabalho de CPU longo dentro dele
bloqueia TODO I/O de rede (recv/send/ping) de TODOS os clients pela duração
do bloqueio — não é problema de rede/banda, é o event loop preso.

Causa raiz: as 6 lanes calculavam pathfinding A* **por minion** (7 minions
× 6 lanes = 42 chamadas de `find_path`, `max_nodes=4000` cada, ver
§34.74.25) e, por só existir 1 timer por lane sem nenhum escalonamento,
todas as 6 disparavam no mesmo tick — 42 buscas A* síncronas empilhadas no
mesmo `_tick`, tempo suficiente pra estourar o timeout de keepalive.

Usuário perguntou sobre "o poder dos vetores" (flow-field) como técnica
pra grande quantidade de inimigos se movendo — validado como técnica real
mas de escopo maior (rota compartilhada por CAMPO em vez de por entidade,
útil se o número de minions crescer muito); adotado como fix somente SE os
2 fixes abaixo não bastarem. Aprovado pelo usuário: implementar os 2 fixes
menores primeiro.

**Fix 1 — 1 rota por LANE, não por minion** (`server/world_server.py::
_tick_minion_waves`): os 7 minions de uma wave têm o MESMO destino/
waypoints e nascem a ±1-2 tiles um do outro — o cálculo de rota
(`for wp in waypoints: pathfinding.find_path(...)`) saiu de dentro do loop
por minion e passou a rodar 1x por lane, a partir do tile CANÔNICO da lane
(`spawn_tile`, sem offset), guardado em `lane_route_tail`. Cada minion só
faz o resto do trabalho que É individual (checar se o tile com offset cai
em parede, via `tile_validation.is_tile_walkable`) e monta sua rota final
como `[(spawn_x, spawn_y)] + lane_route_tail` — reduz ~7x as chamadas de
`find_path` por wave (42 → 6), resultado visual idêntico (mesmo trajeto).

**Fix 2 — Lanes escalonadas por GRUPO (top → bot → mid), 1.5s entre
grupos** (`server/world_server.py::_activate_minion_lanes`): pedido
explícito do usuário foi "não desbalancear os times" — por isso o
escalonamento é por TIPO de lane com os 2 times JUNTOS no mesmo grupo
(nunca 1 time saindo na frente do outro na mesma lane). Novo
`_LANE_GROUP_ORDER = ("top", "bot", "mid")` e `_LANE_GROUP_STAGGER_S = 1.5`
— o timer inicial de cada lane (`_minion_wave_timers[key]`) começa em
`-(group_index * 1.5)` em vez de `0.0`; como o resto da lógica só faz
`elapsed += dt` e compara contra `wave_interval_s`, o delta negativo
atrasa o primeiro disparo (e, por carregar o resto adiante, todos os
seguintes) sem tocar em nenhuma lógica de consumo de trigger. 1.5s foi
avaliado suficiente (grupos somados: 3s de latência total top→mid), ainda
mais combinado com o Fix 1 reduzindo o trabalho de CADA grupo.

**Bug de dado encontrado durante a validação** (não relacionado aos 2
fixes acima, achado pelo teste de rota real): usuário moveu o Nexus do
time B de `(97,2)` pra `(95,4)` durante a sessão — coincidiu exatamente
com o `target_tile` hardcoded da lane A-mid (`[95,4]`), reproduzindo o
MESMO bug de §34.74.25-2 (torre bloqueando o próprio alvo da lane) do
outro lado. Fix: `target_tile` de A-mid voltou pra `[97, 2]` (posição
antiga do Nexus B, walkable e alcançável — confirmado por script antes de
aplicar).

Testes (`tests/test_minions.py::TestMinionWaveSpawning`):
- `test_wave_calcula_1_rota_por_lane_nao_por_minion` — 2 lanes do mesmo
  grupo (`lane_id="top"`, times diferentes), chama
  `_tick_minion_waves(6.5)` DIRETO (não `run_ticks`, pra isolar de outros
  sistemas que também chamam `find_path` no mapa padrão populado por
  `make_world_server()`) — confirma `len(calls) <= 2` (1 por lane) e 14
  minions enfileirados. Prova diferencial: reintroduzida uma chamada
  redundante de `find_path` por minion → teste falhou com 16 chamadas →
  revertido → passa de novo.
- `test_grupos_de_lane_disparam_escalonados_nao_no_mesmo_tick` — mapa e
  gates reais da BG, `wave_interval_s=6.0` uniforme, roda 6.1s — confirma
  que só os minions do grupo "top" existem nesse instante (rota termina
  em `(94,3)`/`(3,94)`), bot/mid ainda não dispararam. Prova diferencial
  confirmada (falha com o stagger desligado).
- `test_2_lanes_do_mesmo_time_disparam_independente_lane_id` e
  `test_todas_as_lanes_reais_da_bg_produzem_rota_valida` (de §34.74.25)
  tiveram a janela de ticks recalculada pra contabilizar o atraso novo de
  +1.5s/+3.0s por grupo.

Suíte completa (`pytest tests/ -q`): 795 passed. Pendente: validação
manual do usuário em playtest real pra confirmar que o freeze/reconexão
não ocorre mais com as 6 lanes ativas simultaneamente.

#### §34.74.27 — Freeze/reconexão VOLTOU a acontecer mesmo com §34.74.26: causa real era MOVIMENTO, não spawn (04/08/2026)

Usuário testou os 2 fixes de §34.74.26 (rota compartilhada por lane +
lanes escalonadas) e reportou: "aconteceu exatamente a mesma coisa" —
mesmo log de `ConnectionClosedError(..., reason='keepalive ping
timeout')`, mesma sessão travando. Hipótese do usuário: "provavelmente o
server calcula o path a cada tick."

Correta em espírito. §34.74.26 só resolveu o burst do SPAWN da wave — a
fonte real e CONTÍNUA (durante toda a travessia da lane, não só no
nascimento) era `MinionSystem._walk_toward` (`engine/world_systems.py`):
ADVANCING mirava um "checkpoint" recalculado a cada 5 tiles de `route`
(mecanismo introduzido em 01/08/2026 pra contornar bloqueio temporário,
ver §34.72). Como o DESTINO mudava a cada checkpoint, `_walk_toward`
recalculava A* toda vez que um chegava (não só quando bloqueado) — e como
os 7 minions de uma wave nascem juntos e andam na MESMA velocidade, seus
checkpoints tendiam a SINCRONIZAR: periodicamente, um monte deles batia o
checkpoint no mesmo tick e todos recalculavam A* juntos, ~a cada 0.8s,
durante toda a lane — o mesmo mecanismo de bloqueio do event loop de
§34.74.26, só que repetido sem fim em vez de só no spawn.

Usuário pediu a simplificação direto (não só o fix pontual): "esquecer a
ideia do checkpoint a cada 5 tiles, pois não tem necessidade, os minions
vão ter só as coordenadas do 'target_tile', e só aqueles que precisarem
vão recalcular a rota" + budget de 15 chamadas de A* por tick.

**Fix 1 — ADVANCING mira SEMPRE `route[-1]` (destino final fixo), nunca
mais um checkpoint intermediário.** `_walk_toward` já tinha a lógica
certa pra isso: só recalcula A* quando `current_path` está vazio E
(`path_dest` mudou OU o path anterior foi TOTALMENTE consumido) — com
destino fixo, isso vira "recalcula 1x no início, anda o resto de graça, só
recalcula de novo se um passo for REJEITADO por bloqueio real"
(`is_tile_walkable` falhar zera `current_path` e força novo cálculo, ver
linha 1883). Ou seja: **"só quem precisa recalcula" já era o
comportamento nativo de `_walk_toward`** — o bug era o checkpoint mudando
o destino artificialmente a cada 5 tiles, forçando recálculo mesmo sem
bloqueio nenhum. Removido `Minion.route_idx` (só existia pra essa conta)
e `MinionSystem.CHECKPOINT_ARRIVAL_RADIUS` (idem).

**Fix 2 — orçamento de 15 chamadas de `find_path` por tick**
(`MinionSystem.MAX_PATHFINDS_PER_FRAME = 15`, `_pathfind_budget` resetado
no topo de `update()`), mesmo padrão já usado por `EnemyAISystem.
MAX_PATHFINDS_PER_FRAME`/`_find_path_budgeted` pro mesmo problema —
protege o caso em que VÁRIOS minions ainda precisam recalcular no MESMO
tick (ex: wave inteira acabou de nascer, ou vários batem em obstáculo ao
mesmo tempo). Quem estoura o orçamento só tenta de novo no PRÓXIMO tick,
sem consumir `path_recalc_timer` (não é tratado como falha).

**Efeito colateral necessário**: como `dest` agora pode ser o
`target_tile` da lane INTEIRA (até ~95 tiles de distância, não mais um
checkpoint de até 5), `_walk_toward` passou a chamar `find_path` com
`manhattan_limit=None, max_nodes=4000` (mesmo budget do cálculo de rota
de wave, §34.74.25) — os limites default (`manhattan_limit=60`)
rejeitariam a busca de cara pra qualquer lane longa.

Teste (`tests/test_minions.py::TestMinionSystemTargeting::
test_walk_toward_respeita_orcamento_de_pathfind_por_tick`): 20 minions
todos precisando de path na primeira chamada de `update()` — confirma no
máximo 15 chamadas de `find_path` nesse tick e que ao menos 1 minion fica
sem path calculado (adiado). Prova diferencial confirmada (desligando o
guard de orçamento, o teste falha com 20 chamadas). Os 3 testes que
dependiam de `route_idx` (`test_ao_perder_alvo_minion_segue_direto_sem_
voltar_ao_checkpoint`, `test_apos_perder_alvo_minion_continua_avancando_
ate_a_base`) foram reescritos pra usar a posição física do minion
(`TileMovement`) em vez do índice removido — mesmo comportamento
verificado, mecanismo novo.

Suíte completa: 797 passed, 1 failed (`test_session.py::TestAOIUpdate::
test_mob_despawn_sent_to_both_players` — não relacionado a Minion/
pathfinding, passou 3x isolado, falha só sob carga da suíte completa;
timing-sensitive pré-existente, não introduzido por este fix).

Usuário testou de novo e o freeze VOLTOU a acontecer — dessa vez a
correlação temporal no log era outra: uma rajada de ~15 mortes de minion
em ~8s (2 waves de times opostos se cruzando no meio da lane, todas as
mortes num raio pequeno de tiles) imediatamente antes da queda de
conexão. Ou seja, a fonte AGORA é volume de PROCESSAMENTO DE COMBATE
(ataques/mortes/loot/XP simultâneos de várias lanes colidindo), não mais
pathfinding. Solução pedida pelo usuário: reduzir a composição da wave —
`WorldServer._MINION_WAVE_COMPOSITION` foi de `3 melee + 3 ranged + 1
raro` (7/wave) pra `1 melee + 2 ranged + 1 raro` (4/wave) — com as 6
lanes ativas, o pico simultâneo de minions cai de 42 pra 24. Todos os
testes que contavam minions por wave (`test_wave_spawna_composicao_
exata_1_2_1` — renomeado de `..._3_3_1` —, `test_minions_da_wave_nascem_
escalonados_...`, `test_wave_intervalo_configuravel_...`,
`test_2_lanes_do_mesmo_time_disparam_...`, `test_todas_as_lanes_reais_
da_bg_produzem_rota_valida`, `test_wave_calcula_1_rota_por_lane_nao_por_
minion`) foram recalculados pra 4/wave.

**Achado incidental durante o ajuste**: `test_xp_do_minion_escala_com_o_
level` (`TestMinionDeathXpGold`) começou a falhar — não por nada relacionado
a pathfinding/composição, mas porque o usuário já tinha ajustado
`content/minion_definitions.py::MINION_TABLE["minion_melee"]["xp_reward"]`
(balanceamento, edição concorrente) pra um valor onde `xp_reward × level
do teste (3) = 135 > CharacterStats.BASE_XP (100)` — o teste comparava
`current_xp` CRU contra o total esperado, mas um level-up no meio
CONSOME o threshold de `current_xp` (`stats_system.py::_apply_levelups`),
deixando só o excedente (35, não 135) — a asserção quebrava mesmo com o
cálculo de XP correto, só por cruzar um threshold de level. Fix: teste
agora reconstrói "XP total ganho" somando o que foi consumido em cada
level-up (`CharacterStats.xp_for_level(level)` por nível cruzado) + o
`current_xp` final, tornando-o imune a futuras mudanças de balanceamento
de `xp_reward` (classe de bug igual à do target_tile colidindo com torre
— dado de conteúdo/mapa editado pelo usuário em paralelo invalidando uma
suposição implícita de um teste).

#### §34.74.28 — CORREÇÃO do §34.74.27: minions de TODAS as lanes convergiam pro mid (04/08/2026)

Usuário testou §34.74.27 (ADVANCING mirando `route[-1]` fixo) e reportou
2 problemas: o freeze ainda ocorria (rajada de mortes de minion, ver nota
já registrada em §34.74.27), E "os minions não estão indo pra sua rota,
todos estão indo pro mid" — mesmo com as coordenadas dos waypoints de
top/bot corretas no JSON (intermediário `[5,5]`→`[92,5]` pro top,
`[94,94]`→`[94,7]` pro bot).

**Diagnóstico por simulação** (não só leitura de código — rodado contra
o mapa/JSON REAIS): o cálculo da rota no spawn da wave (`_tick_minion_
waves`) está correto — cada lane termina no tile certo, contornando a
selva central pela borda como desenhado. O bug é em MOVIMENTO: §34.74.27
fez ADVANCING mirar `route[-1]` (só o destino final) direto via
`_walk_toward`, que roda A* **livre e de longa distância** a partir da
posição FÍSICA atual do minion. Este mapa da BG tem um "gargalo" —
selva/rio central com só 1 passagem livre (o corredor do mid) ligando os
2 lados, com uma faixa aberta contornando toda a BORDA (onde top/bot
foram desenhados pra andar). Um A* livre, buscando o caminho
objetivamente mais curto entre QUALQUER ponto do mapa e um destino
distante, quase sempre atravessa essa única passagem central — então
TODAS as lanes, mesmo com o polyline por-lane calculado certo no spawn,
convergiam fisicamente pro mesmo ponto ao ANDAR (confirmado por
simulação: minions de top/mid/bot, dos 2 times, todos fisicamente num
cluster em `(41-59, 39-61)` — bem no meio do mapa — após 20s de
caminhada real).

**Fix**: `Minion.route_idx` volta a existir (não mais como índice de
"checkpoint a cada 5", e sim como progresso REAL no polyline) e
`MinionSystem._advance_along_route` (novo) consome `route` tile a tile
de verdade:
- Caso normal: só checa se `route[route_idx+1]` está adjacente e
  andável (`is_tile_walkable`) — **zero pathfinding**, mais barato até
  que o design anterior a §34.74.27.
- Só cai pro desvio LOCAL (`_walk_toward`, já orçado em
  `MAX_PATHFINDS_PER_FRAME=15`) quando bloqueado de verdade OU o minion
  está fisicamente NÃO-adjacente ao próximo tile da rota (ex: voltando
  de perseguir um alvo em FIGHTING) — e mesmo aí, mira um ponto só
  `ROUTE_LOOKAHEAD_TILES=6` à FRENTE NA PRÓPRIA ROTA, nunca o destino
  final — nunca dá ao A* raio de busca suficiente pra redescobrir o
  atalho global pelo meio do mapa.
- `_walk_toward` voltou aos limites DEFAULT do A* (`manhattan_limit=60,
  max_nodes=300`, revertido do `None`/`4000` de §34.74.27) — `dest`
  agora é sempre local (lookahead de 6 tiles ou um alvo em combate
  dentro do raio de aggro), nunca precisa de busca de longo alcance.

Teste novo (`tests/test_minions.py::TestMinionWaveSpawning::
test_minion_fisicamente_segue_a_propria_rota_nao_atalho_pelo_meio`):
mapa/entities REAIS da BG, roda a wave completa + 20s de caminhada real,
confirma que cada minion fica a no máximo 15 tiles (chebyshev) de ALGUM
tile do próprio `route` — pega exatamente o sintoma "convergiu pro
centro do mapa". Prova diferencial: reintroduzido o mecanismo exato de
§34.74.27 (mirar `route[-1]` direto + limites amplos no `find_path`) →
teste falha reproduzindo o sintoma quase idêntico ao achado manual
(minion a 42 tiles da própria rota, `pos=(46,50)`) → revertido, passa de
novo. `test_walk_toward_respeita_orcamento_de_pathfind_por_tick`
também precisou ser redesenhado: o caso comum não chama mais
`find_path` nenhum (fast-path sem pathfinding), então o teste agora
força `route_idx` adiantado artificialmente (simula "fora da rota") pra
exercitar de verdade o orçamento de 15/tick.

Suíte completa: 799 passed (limpa, sem os flakes pré-existentes desta
vez). Pendente: usuário testar em jogo com a composição atual (4/wave,
§34.74.27) — se o freeze não voltar, reverter a composição pra 3+3+1
(7/wave) por último, já que a causa raiz de pathfinding foi endereçada
independentemente da contagem de minions.

#### §34.74.29 — Breakdown por-sistema no profiler de tick, pra achar a origem de travadas pontuais (04/08/2026)

Usuário confirmou que §34.74.28 corrigiu o roteamento das lanes (cada
wave foi pra sua própria lane), mas ainda sentiu travadas ocasionais em
playtest, com o indicador de ping da HUD passando de 600ms. Pediu um
monitoramento gravado em log pra avaliar quanto cada sistema consome/
impacta a latência.

**Achado**: já existia um profiler de tick bem construído
(`WorldServer.__init__`, campos `_perf_accum`/`_perf_count`/
`_perf_cpu_sum`/etc., gravando em `logs/server_perf.log`) — com relatório
periódico a cada `_PERF_REPORT_TICKS=300` ticks (~10s) mostrando ms/tick
médio por seção, e uma linha `[PERF] tick lento` sempre que um tick
individual passa de `_PERF_BUDGET_MS=33.3ms` (1 tick a 30/s). Só que 2
lacunas: (1) o profiler só cobria ~6 seções (`ai_bundles`, bundles por
mapa, `global_systems`, `skill_requests`, `spell_completions`,
`aoi_collect`) — boa parte do corpo de `_tick` (combate direto, morte,
loot, torre, **minion**, trade/duelo/arena) não tinha NENHUMA medição;
(2) a linha de "tick lento" só mostrava o total em ms, nunca QUAL seção
causou aquele pico específico — só dava pra saber via a média do
relatório de 10s inteiro, que dilui um pico raro e pode nem apontar o
sistema certo se o pico foi atípico.

**Fix**:
- `WorldServer._perf_mark(label, t0)` (novo, chokepoint único) — grava o
  tempo decorrido em `_perf_accum` (média periódica, como antes) E em
  `_perf_tick_now` (novo dict, resetado no INÍCIO de cada `_tick()` —
  snapshot só do tick atual). Os 6 pontos de medição existentes foram
  retrofitados pra usar `_perf_mark` em vez de escrever nos 2 dicts na
  mão.
- **Cobertura nova** (mesmo padrão `_t0p = perf_counter(); <chamada>;
  self._perf_mark("label", _t0p)`): `player_attacks`, `death_handling`
  (do `_death_handler.update()` até o fim do loop de XP/level-up),
  `loot_drops`, `harvestable_tower_respawns` (respawn de harvestable +
  zonas + torre agrupados), `tower_system`, **`minion_system`**,
  **`minion_waves`**, **`minion_spawn_queue`** (as 3 seções mais
  suspeitas dado todo o histórico desta sessão), `trade_duel_arena_ticks`
  (trade/duelo/fila e pendências de arena agrupados), e
  `post_tick_bookkeeping` (detecção de mob novo/movimento/projétil pro
  broadcast).
- **Breakdown no pico**: `_PERF_BREAKDOWN_MS=100.0` (bem acima do budget
  de 33.3ms de propósito — um tick real sob carga passa um pouco do
  budget o tempo todo, isso sozinho não é "travada"; o breakdown só vale
  a pena pra picos de verdade). Quando um tick passa desse threshold, a
  linha `[PERF] tick lento` ganha um sufixo `| TOP: <label>=<ms> | ... |
  outros=<ms>` com as até 8 seções que mais pesaram NAQUELE tick
  específico (de `_perf_tick_now`, não a média) — dá pra correlacionar
  direto "travada às 21:47:03" → "minion_system=580ms".

Testes (`tests/test_server.py::TestTickPerfProfiler`, novo): `_perf_mark`
grava nos 2 dicts; `_perf_tick_now` reseta a cada tick enquanto
`_perf_accum` continua somando (prova diferencial com sleep conhecido de
0.15s num sistema real — 2 ticks seguidos ficam ~0.30s acumulados em
`_perf_accum` mas cada `_perf_tick_now` individual fica ~0.15s, nunca
0.30s; desligando o reset de propósito, o teste falha com ~0.30s no
`_perf_tick_now`, confirmando que ele pegaria a regressão); linha de
tick lento aponta corretamente o sistema tornado lento de propósito
(`MinionSystem.update` com sleep injetado) como o principal consumidor
do pico.

Suíte completa: 802 passed. Pendente: usuário rodar em playtest real e
mandar os trechos de `logs/server_perf.log` em torno de uma travada
percebida — o breakdown deve apontar exatamente qual sistema (ou
confirmar que NENHUM tick individual do servidor está estourando 100ms,
apontando a causa pra outro lugar: rede, cliente, ou o próprio SO).

#### §34.74.30 — Log de perf real apontou tower_system/minion_system: varredura global de alvo virou índice espacial (04/08/2026)

Usuário mandou `logs/server_perf.log` de um playtest real na BG. Análise
confirmou: `minion_waves`/`minion_spawn_queue` (spawn de wave,
pathfinding de rota — §34.74.26-28) ficaram em ~0.00-0.02ms, validando
os fixes anteriores. O custo migrou pra outro lugar: assim que a BG
ficava ativa e o número de minions crescia, `tower_system` (~1.5ms →
até 11ms, 51.8% do tick) e `minion_system` (~0.01ms → até 13.9ms, 41.3%
do tick) dominavam. A MÉDIA do tick passou de 33ms em várias janelas de
10s (não só picos isolados — sobrecarga sustentada por segundos
seguidos, batendo com "senti umas travadas"), com picos de até 124ms.

**Causa**: `TowerSystem._acquire_target`/`MinionSystem._acquire_target`
faziam uma varredura **global** de TODAS as entidades combatentes do
jogo (`Position+CombatStats+TileMovement`, sem filtro espacial) — torre
só quando perde o alvo (sticky), mas minion em ADVANCING faz isso TODO
TICK enquanto não tiver alvo. Com dezenas de minions de BG × centenas de
entidades no mundo todo, isso virava milhares de iterações/tick.

Usuário propôs a solução direto: "os minions tem um range de aggro, não
seria viável só varrer o que está dentro dessa área, e pular se já tiver
alvo?" — confirmado: "pular se já tem alvo" **já era** o comportamento
(torre é sticky; minion só varre em ADVANCING, nunca em FIGHTING). A
parte real da lacuna era "só varrer a área". Pesquisa confirmou ser a
técnica padrão da indústria (spatial hashing/grid pra broad-phase —
[Sparse Spatial Hash Grids](https://metafunctor.com/post/2025-11-11-sparse-spatial-hash/),
[Collision Detection and Spatial Indexes](https://kortham.net/posts/collision-detect-and-spatial-indexes/)),
e o projeto já tinha essa peça pronta: `engine.utils.SpatialHash`, já
usado por `server/session.py` pro filtro de AOI de sessão.

**Fix**:
- `engine/world_systems.py::_combat_candidates_near` (novo, chokepoint
  único) — dado um `spatial_hash: dict[map_file, SpatialHash] | None`,
  devolve só os candidatos nas células vizinhas ao raio pedido; sem
  hash (`None`), cai no sweep completo de sempre (fallback pra testes
  headless que criam `TowerSystem`/`MinionSystem` direto, sem
  `WorldServer` real).
- `TowerSystem._acquire_target`/`MinionSystem._acquire_target`: trocam
  `world.get_entities_with(Position, CombatStats, TileMovement)` por
  `_combat_candidates_near(...)` — os MESMOS filtros de sempre (hp>0,
  hostil, visível, LOS) continuam aplicados, só que num conjunto bem
  menor.
- `WorldServer._tick`: constrói `_combat_spatial_hash` (dict `map_file`
  → `SpatialHash`, `cell_size=9` — cobre o maior alcance real, torre
  `attack_range_tiles=8`) **1x por tick**, O(entidades) — REUTILIZADO
  por `TowerSystem.update`/`MinionSystem.update` via novo parâmetro
  `spatial_hash=...` (mesmo padrão de `combat_this_tick`, já passado
  fresco a cada tick). 1 índice só serve os 2 sistemas — mesmo shape de
  candidato que os dois já procuravam.

Testes (`tests/test_towers.py::TestTowerTargeting::
test_acquire_target_com_spatial_hash_nao_varre_todas_as_entidades`,
`tests/test_minions.py::TestMinionSystemTargeting::
test_acquire_target_com_spatial_hash_nao_varre_todas_as_entidades`): 1
hostil dentro do alcance + 30 bem longe — confirma que o alvo certo
ainda é achado E que `world.get_entities_with` (sweep completo) nunca é
chamado quando o índice é fornecido (espiona a chamada). Prova
diferencial: desligando o uso do hash (`if False and spatial_hash...`),
o teste falha detectando a chamada do sweep completo — revertido, passa
de novo.

Suíte completa: 804 passed. Verificação manual (script, mapa/entities
reais da BG): sem player, com `wave_interval_s=6.0` artificialmente
rápido (10x mais agressivo que o padrão de produção, 45s — só pra testar
rápido) por 150s simulados, `tower_system` caiu de ~9-11ms (log real)
pra ~1.0ms — confirma a melhora. `minion_system` ainda apareceu alto
(~12ms) NESSE teste específico, mas com 273 minions concorrentes vivos
(vs os ~50-60 de BG que o log real tinha) — população ~5-10x maior só
por causa do wave_interval artificialmente agressivo do teste, não
prova de que o fix não funcionou (o custo restante é O(minions) da
própria iteração externa de `MinionSystem.update`, inevitável e
independente da varredura de alvo que foi corrigida). Comparação justa
precisa de um playtest real (wave_interval de produção) — pendente:
usuário testar de novo e mandar um log de perf fresco pra confirmar a
melhora com população realista de minions.

#### §34.74.31 — Fila REAL de matchmaking da BG estilo MOBA (04/08/2026)

Usuário pediu o equivalente à fila de Arena (`server/match_processor.py`)
pra BG, com 3 diferenças centrais em relação a ela: (1) fila ÚNICA, sem
escolher modo — solo ou grupo já formado, a fila decide o TAMANHO do
time sozinha; (2) NUNCA assimétrico, 1x1 até 5x5; (3) "maior partida
possível agora" — antes de fechar 1x1 com 2 solos, tenta ver se dá pra
fechar algo maior com quem já está esperando.

**Arquitetura** (novo `server/bg_queue_processor.py`, `BgQueueProcessorMixin`,
mixado em `WorldServer` junto de `MatchProcessorMixin`): espelha a FORMA
da Arena (fila → propõe → aceite → countdown → portão → luta → Nexus
derrubado → sai), reaproveitando ao máximo peças já prontas:
- `PartyProcessorMixin` pro conceito de "grupo pré-formado" — igual
  Arena, um grupo INTEIRO vira 1 "time" (nunca dividido entre os 2
  lados). `PARTY_MAX_SIZE=5` já bate exatamente com "até 5x5".
- `WorldServer._load_instance`/`_unload_instance`, chave POR PARTIDA
  (`f"{template}::{match_id}"`, igual Arena) — múltiplas partidas da
  fila real coexistem, cada uma isolada, diferente da instância ÚNICA
  compartilhada de `server/debug_battleground.py` (`/testbg`, mantido
  INTOCADO — os dois sistemas coexistem, `/testbg` continua servindo
  debug solo rápido sem precisar de 2+ clientes).
- Geometria do mapa (spawn/portão) IMPORTADA de `debug_battleground.py`
  (`DEBUG_BG_TEMPLATE`/`DEBUG_BG_SPAWN`/`DEBUG_BG_GATE_TILES` — mesmo
  mapa físico pros 2 sistemas, reaproveitar evita 2 listas de tile
  podendo divergir).
- `server/instance_progression.py::enter_/exit_normalized_progression`
  — **primeira vez que um processador de jogo de verdade chama isso**
  (até aqui só "/testbg" ativava o módulo, documentado como INERTE).
- Fim de partida por NEXUS destruído (não eliminação de time — MOBA,
  respawn automático, não Arena) + HUD de KDA ao vivo — mesmo padrão de
  `debug_battleground.py`, mas por PARTIDA em vez de 1 estado global.

**Algoritmo de matchmaking** (`_tick_bg_queue`/`_bg_try_pack`): token =
`("solo", eid)` ou `("party", party_id)` — nunca ambíguo (diferente de
usar o int cru, que poderia colidir entre um eid e um party_id de
contadores independentes). A cada tick, tenta `team_size` de 5 até 1:
bin-packing guloso (preenche o lado A, depois o B, só aceita tokens que
cabem no `team_size` tentado) — só COMITA se os 2 lados fecharem
EXATAMENTE o mesmo tamanho (nunca assimétrico, mesmo que o empacotamento
guloso não seja o teoricamente ótimo — nesse caso só demora mais um
tick pra fechar). Depois de formar uma partida, tenta formar MAIS na
MESMA passada (recursivo) — 10 solos esperando viram 1 partida 5x5, não
5 partidas 1x1.

**Correção necessária, achada no caminho**: `debug_battleground.py::
notify_nexus_destroyed` supunha que só existia UMA instância do mapa
MOBA por vez — com partidas reais da fila coexistindo (mesmas strings de
facção "arena_time_a/b" em CADA instância), uma torre caindo numa
partida da fila ficaria ambígua com a instância de debug se as duas
estivessem carregadas. Fix: `notify_nexus_destroyed` ganhou um parâmetro
`tower_map_file` — só reage se bater com a PRÓPRIA instância
(`DEBUG_BG_INSTANCE_KEY`). `server/server_death_handler.py`'s hook (torre
`is_nexus` morta) agora notifica os DOIS sistemas
(`debug_battleground.notify_nexus_destroyed` E `WorldServer.
notify_bg_queue_nexus_destroyed`), cada um decidindo sozinho pelo
map_file se a torre é dele.

**Protocolo** (`shared/messages.py`): `BG_QUEUE_JOIN/LEAVE/STATE`,
`BG_MATCH_FOUND`, `BG_MATCH_ACCEPT`, `BG_MATCH_START`, `BG_MATCH_LEAVE`
— só o necessário; reaproveita `ARENA_COUNTDOWN`/`ARENA_GATE_OPEN`
(já generalizados pra debug_battleground.py) e `BG_MATCH_RESULT` (já
existia, criado junto do placar de Nexus de `/testbg`) em vez de
duplicar um protocolo BG_* paralelo pra essas 3.

**Cliente** (novo `client/bg_queue_handlers.py`): estado de fila (sem
"modo"), modal de aceite PRÓPRIO (`_draw_bg_accept_modal` — payload
diferente do de Arena, `team_size` variável em vez de `mode` fixo, por
isso não reaproveita o modal de aceite da Arena). A linha "Battleground
(MOBA)" dentro do MODAL UNIFICADO de fila é desenhada por
`client/arena_handlers.py::_draw_arena_queue_modal` (mesmo container que
já lista Arena 1v1/2v2/3v3 — extensão pontual, elegibilidade sempre
`True`, sem checagem de "grupo do tamanho exato" que as linhas de Arena
têm). `game.py::_client_pvp_context` ganhou um bloco a mais (`_bg_in_match`/
`_bg_opponents_server`, mesmo racional exato do bloco de Arena — cliente
nunca recebe `Faction` de outro player via `ENTITY_SPAWN`, só mob manda
"faction", então precisa de uma lista explícita de oponentes pra
liberar clique-direito/SPACE/skill). O modal de RESULTADO (Nexus
derrubado) já existia pronto (`client/battleground_handlers.py`,
`BG_MATCH_RESULT` já genérico) — só `_send_bg_leave` ganhou um branch
(`_bg_in_match` real → `BG_MATCH_LEAVE`; senão → `/testbg leave`, de
sempre).

**Abertura do modal** (pedido do usuário — substitui o botão "Fila de
Arena" fixo do HUD, REMOVIDO): tecla **F1** (`game.py`) ou comando de
chat **"/bgqueue"** (`client/chat_handlers.py`, mesmo padrão de
interceptação de `/convidar`/`/forfeit`) — os dois chamam a MESMA
`_open_bg_queue_modal()` (toggle: fecha se já aberto; não abre se já em
partida/aguardando aceite, de Arena OU de BG).

Testes: `tests/test_bg_queue.py` (33 casos — fila join/leave, algoritmo
de empacotamento incluindo mistura grupo+solo e "nunca assimétrico" com
prova diferencial, ciclo de vida completo, Nexus/roteamento por
instância, saída/timeout/desconexão) + `tests/test_bg_queue_client_ui.py`
(19 casos — estado de fila, modal de aceite, linha no modal unificado,
abertura via F1/comando, com prova diferencial no gate "não abre se já
ocupado"). Suíte completa: 856 passed.

#### §34.74.32 — Mapa vazio custava CPU todo tick + orçamento de pathfind compartilhado entre partidas concorrentes (04/08/2026)

Usuário testou a fila de BG real (§34.74.31) com 2 contas e pediu análise
do `logs/server_perf.log`. Primeiro achado (`tower_system`/`minion_system`
subindo de 1→2 players) levou a uma investigação maior a pedido do
usuário: "quero uma solução definitiva... nem que envolva trabalho grande
e complexo", incluindo pesquisa de arquitetura de servidor de jogo em
produção (tick rate de MOBA, scheduler de pathfinding, sharding por
partida — ver fontes na resposta original, não citadas aqui).

**Achado principal do usuário, confirmado por código**: mapas SEM NENHUM
player conectado (`bnd:map_cave_west`/`bnd:map_cave_east` no profiler)
continuavam custando ~1-2.5ms/tick cada, com `[ativo 0/300 ticks, 0p
agora]`. Diagnóstico inicial (culpar `EnemyAISystem`/`EnemyAbilitySystem`)
estava ERRADO — esses dois já são pulados sem player via
`_MapBundle.ai_systems` (`server/world_server.py::_tick`, linha ~3864:
`if not _has_player and system in _bnd.ai_systems: continue`). Os vilões
reais, fora desse gate:

1. **`TileValidationSystem.update()`** (`engine/world_systems.py`) — 1
   instância por bundle, reconstruía seu cache `_occupied` via scan
   GLOBAL de `get_entities_with(TileMovement)` (todo o mundo, ~190+
   entidades) todo tick, incondicional — mas esse cache só é consultado
   por pathfinding/AI, que já ficam fora do ar sem player. Fix: entra no
   MESMO `ai_systems` que já pulava EnemyAISystem/EnemyAbilitySystem
   (`bundle.ai_systems = {enemy_ai_system, enemy_ab_system,
   tile_validation}`).
2. **`SpawnZoneSystem.update()`** (`ACTIVATION_RADIUS=999999` forçado na
   criação do bundle — zonas nunca são puladas por distância) fazia o
   MESMO scan global de `TileMovement`, redundante com o de cima,
   incondicional — mas só é LIDO dentro de `_pick_tile()`, chamado SÓ
   quando um timer de respawn realmente zera. Pergunta do usuário ("já
   respawnou tudo, por que rodar de novo?") levou ao fix real: o scan
   virou preguiçoso (`_get_occupied()`, fechamento local com cache
   `_occupied_cache`) — só constrói se algum spawn for de fato necessário
   este tick (zona cheia, sem timer pendente → zero scan). Timer/detecção
   de morte continuam rodando todo tick, normalmente (baratos, listas
   pequenas por zona) — respawn nunca para de funcionar sem player.
3. **`combat_spatial_hash`** (`server/world_server.py::_tick`) hasheava
   TODO combatente de TODO mapa, mas só `TowerSystem`/`MinionSystem`
   consultam essa hash, e Tower/Minion só existem em instância de
   Battleground. Fix: só insere entidade cujo `map_file` está em
   `self._minion_lanes` (mesmo sinal que `_create_minion_lanes` já grava
   pra mapas com lane registrada — nunca reimplementado, só reaproveitado).
4. **`MinionSystem.MAX_PATHFINDS_PER_FRAME`** era um orçamento ÚNICO
   compartilhado por TODAS as partidas de BG concorrentes — uma partida
   podia esgotar o orçamento de pathfind de outra partida ativa ao mesmo
   tempo (achado ao investigar um cluster de ~24 ticks lentos seguidos no
   log — 2 lanes de facções opostas disparando wave no mesmo instante).
   Fix: `_pathfind_budget` vira `dict[map_file, int]`, criado sob demanda
   dentro de `_walk_toward` (nunca precisa saber os mapas ativos de
   antemão) — cada instância de BG tem seu próprio orçamento.

**Confirmado por código, não é exponencial por jogador**: `
_activate_minion_lanes` ativa as 6 lanes/20 torres do template
INDEPENDENTE de `team_size` — uma partida 1x1 custa o MESMO que uma 5x5.
O risco real de escala é NÚMERO DE PARTIDAS CONCORRENTES (item 3/4
acima), não o tamanho de cada partida.

Testes: `tests/test_idle_map_perf.py` (7 casos, prova diferencial em
CADA um dos 4 itens acima — `DIFFERENTIAL-PROOF-TEMP` revertido e
confirmado que falha antes de restaurar): TileValidationSystem não roda
em mapa sem player (mas roda normal em mapa com player), SpawnZoneSystem
não escaneia quando a zona já está cheia (mas escaneia quando um timer
vence), combat_spatial_hash não insere entidade de mapa sem lane
registrada (mas insere quando tem), orçamento de pathfind de uma partida
não é roubado por outra partida em mapa diferente. Suíte completa: 863
passed (3x limpa).

#### §34.74.33 — INCIDENTE: hotbar/consumíveis corrompidos pra sempre por autosave/fechar o jogo dentro da BG (04/08/2026)

Usuário relatou, testando a fila real de BG: "a barra de ações do
personagem fica totalmente desconfigurada depois que o personagem sai da
BG, e a bag também está sofrendo alterações... seja por qualquer motivo,
fechar o jogo enquanto está na BG ou qualquer outra coisa" — mesma
gravidade dos incidentes §34.74.15 (progressão real sobrescrita no
banco)/§34.74.19 (bag perdida por race de mensagens), mas os DOIS já
tinham sido corrigidos e o guard deles (`is_in_normalized_progression`)
continuava funcionando — o servidor nunca escreveu dado de instância no
banco desta vez.

**Causa raiz real (client-side, nunca coberta pelos fixes anteriores)**:
`game.py::_save_config()` — chamada de **17 pontos diferentes** do
cliente (autosave periódico, fechar o jogo, editor de hotbar, comprar
consumível, etc.) — serializa `PlayerSkills.skills`/`ConsumableBar.slots`
AO VIVO pra `config.json` (`_hotbar_to_dict()`/`_consumable_bar_to_dict()`)
sem checar se o player está dentro da BG. Enquanto dentro, esses
componentes guardam o estado TEMPORÁRIO da instância (poucas skills,
ordem sequencial; consumíveis trocados, ver §34.74.13). Um autosave (ou
fechar o jogo) nesse meio tempo — bem provável numa partida que dura
minutos — grava esse estado temporário PERMANENTEMENTE em `config.json`,
por cima do layout real. Na próxima vez que a hotbar é reconstruída
(`_apply_hotbar_config`, só roda no login), ela usa esse `config.json`
corrompido: parece "esqueceu skill"/"desconfigurou" — a conta real no
banco nunca foi tocada, só a preferência local do jogador.

**Fix — chokepoint único** (mesma filosofia de §34.74.15, agora no
cliente): `_save_config()` ganha 1 guard — enquanto
`InstanceInventoryUIState.active` (flag já existente, eco de
`in_instance` do servidor), PRESERVA o que já estava salvo em
`config.json` pra hotbar/consumable_bar (não grava, não manda
`HOTBAR_UPDATE`) — resto do config (volume/scale/menu_keybinds/etc)
continua salvando normal. 1 guard cobre os 17 call sites de uma vez, sem
precisar tocar em nenhum deles.

**Pedido novo, mesmo round**: "a config de atalhos do player deve
permanecer dentro da BG — se uma skill está no slot 3 e for a primeira a
ser desbloqueada, ela deve aparecer no slot 3." `server/
instance_progression.py::_grant_instance_skill` sempre usava "primeiro
slot vazio", ignorando o slot que a skill ocupa na hotbar REAL. Fix:
`_real_slot_by_skill_id(real_ps)` monta um mapa skill_id→índice a partir
da hotbar real do player (já disponível no servidor, sincronizada via
`HOTBAR_UPDATE` — `config.json` é 100% client-local, não precisa virar
protocolo novo) — `_grant_instance_skill` usa esse slot preferido quando
livre, cai pro "primeiro vazio" (comportamento original) se a skill nunca
foi colocada numa hotbar real. Os 2 call sites (`enter_normalized_
progression`, `_process_instance_levelup`) passam o mapa.

Testes: `tests/test_hotbar_bg_leak.py` (5 casos, prova diferencial nos 2
fixes): `_save_config()` preserva hotbar/consumable_bar reais na BG (mas
grava normal fora dela, e outras settings tipo `scale` continuam
salvando mesmo dentro); skill concedida na instância nasce no MESMO slot
da hotbar real (mas cai no primeiro vazio se nunca teve slot real).
Suíte completa: 868 passed (3x limpa).

#### §34.74.34 — CORREÇÃO do §34.74.33: causa raiz real do "slot preferido não funcionou" — `PlayerSkills` do servidor ficava congelado desde o login (05/08/2026)

Usuário testou o fix de §34.74.33 e reportou que não funcionou: moveu a
skill "Recarregar" pra tecla R pouco antes de entrar na BG, ela nasceu na
tecla 1 dentro da instância; ao SAIR, a hotbar ficou "toda bagunçada"; ao
relogar, todas as skills apareceram mas fora da ordem configurada antes
de entrar.

**Causa raiz real**: `server/session.py::_handle_hotbar_update` — chamado
toda vez que o cliente edita a hotbar — só atualizava
`session.last_client_payload` (cache pro PRÓXIMO save completo). Nunca
escrevia no `PlayerSkills` AO VIVO do ECS. Esse componente ao vivo só é
populado uma vez, no login (`spawn_player`) — qualquer edição de hotbar
feita DURANTE a sessão (sem relogar) ficava invisível pro resto do
servidor. Dois consumidores dependiam desse componente pensando que
refletia o estado atual, e os dois quebravam:
1. `_real_slot_by_skill_id` (§34.74.33) consultava esse `PlayerSkills`
   pra achar o slot real de cada skill — lia o layout de ANTES da edição
   do usuário (do login), nunca a edição recente. "Recarregar" não
   aparecia lá no slot certo → caía no fallback "primeiro slot vazio".
2. `enter_normalized_progression` snapshota esse MESMO `PlayerSkills`
   como "o real" pra restaurar depois — `exit_normalized_progression`
   devolvia esse layout desatualizado (não o da instância, mas também
   não o que o usuário tinha configurado) por cima do que o cliente
   realmente tinha na hora de entrar. Como `PlayerSkills.keybinds` do
   CLIENTE nunca é tocado pela sincronização de instância (só `.skills`
   muda), o resultado visual é layout/tecla dessincronizados — "tudo
   bagunçado". E como o fix de §34.74.33 agora deixa `_save_config()`
   gravar normal assim que sai da BG, esse layout desatualizado (não mais
   o da instância, mas ainda errado) acabava sendo persistido em
   `config.json` de verdade na próxima chance — daí "reloguei e não ficou
   como configurei antes de entrar" (nada foi perdido de verdade — as
   skills aprendidas continuam completas no banco — só a ORDEM ficou
   errada).

**Fix — único ponto de verdade**: `_handle_hotbar_update` agora TAMBÉM
aplica a mudança no `PlayerSkills` AO VIVO (além do cache de save, que
continua existindo — não removido). Reconstrói via `PlayerSkills.
_make_skill` só o slot que realmente mudou de conteúdo (mesmo espírito
da reconciliação por-slot do cliente, §34.74.23 — preserva o objeto
existente se o `skill_id` do slot não mudou). **Guard de segurança**:
NUNCA concede skill nova por este canal — só reordena um `skill_id` que
já está em `learned_skill_ids`; um slot pedindo um skill_id não aprendido
é ignorado (sem isso, um cliente malicioso podia mandar
`HOTBAR_UPDATE{skills:["skill_que_não_tem"]}` e ganhar a skill de graça).
Como o componente "ao vivo" tocado é sempre o QUE ESTIVER ATUALMENTE
anexado ao eid (o real fora da BG, o `new_ps` temporário da instância
dentro dela — trocados por `enter_/exit_normalized_progression`), editar
a hotbar DENTRO da BG naturalmente só mexe na cópia da instância, nunca
vaza pro real — nenhum guard extra de instância precisou ser adicionado
aqui.

Testes: `tests/test_hotbar_bg_leak.py::TestHotbarUpdateAppliesLive` (3
casos novos, prova diferencial em cada um — incluindo o guard de
segurança revertido, confirmado que concede a skill não aprendida antes
de restaurar): reordenar via `HOTBAR_UPDATE` reflete no `PlayerSkills` ao
vivo na hora (sem precisar relogar); não concede skill não aprendida;
ponta a ponta — edita a hotbar por mensagem de rede e SÓ DEPOIS entra na
BG, skill de instância nasce no slot recém-configurado. Suíte completa:
871 passed (3x limpa).

#### §34.74.35 — Travamento real numa troca de lane: log.info() síncrono por morte/XP/loot (05/08/2026)

Usuário reportou travamentos num playtest da BG. Log de perf confirmou:
cluster de ~15 ticks lentos seguidos em `moba_battleground::debugtest`
(tick#11540-11693), picos de **111ms/177ms/141ms/124ms** (budget 33ms),
com `death_handling`/`loot_drops` dominando o TOP breakdown — todo o
resto do tick (ai_bundles, minion_system, tower_system,
combat_spatial_hash_build) continuava barato nos MESMOS ticks, então não
era nada relacionado às otimizações anteriores desta sessão (§34.74.32).

**Causa raiz**: 4 chamadas `log.info()` SÍNCRONAS dentro do loop de
processamento de morte — `server_death_handler.py::update` (`[Death]`,
1x por morte), `world_server.py` (`[XP]`, 1x por ENTRADA de xp — XP de
minion por proximidade gera uma entrada POR PLAYER perto, então uma
morte pode virar várias linhas; `[LevelUp]`, 1x por level-up), e
`loot_processor.py::_process_loot_drops` (`[Loot]`, 1x por corpse).
`server/log.py` manda cada `log.info()` pra 2 handlers síncronos
(console `StreamHandler` + arquivo `RotatingFileHandler`) — escrita de
console no Windows é lenta por natureza (pior ainda se a janela do
terminal tiver foco/seleção de texto, que pausa o processo até soltar).
Um choque de lane com várias mortes quase simultâneas virava dezenas de
chamadas de I/O síncrono na MESMA tick, bloqueando o tick de verdade.
Investigado e descartado como causa: o save-por-kill de XP
(`_persist_character` via `asyncio.ensure_future`) já é assíncrono de
verdade (thread separada via `run_in_executor`, `server/auth.py::
save_character`), não bloqueia o tick.

**Fix**: as 4 chamadas rebaixadas de `log.info` pra `log.debug` — morte/
XP/level-up/loot são eventos de ALTA frequência numa BG com minions
(diferente de login/disconnect, que continuam `info` — baixa frequência,
vale a pena sempre ver). Com `RPG_LOG_LEVEL` no default (INFO), essas
linhas nem chegam nos handlers — custo ~zero. Continuam disponíveis
ligando `RPG_LOG_LEVEL=DEBUG`.

Teste: `tests/test_death_log_perf.py` — mata um mob real (`PendingDeath`
+ `damage_log` + 1 tick real) e espiona `logging.Logger.info`/`.debug`:
nenhuma linha `[Death]`/`[XP]`/`[LevelUp]`/`[Loot]` sai em INFO, mas
`[Death]` continua saindo em DEBUG (garante que a informação não sumiu,
só desceu de nível). Prova diferencial confirmada (revertido `[Death]`
pra `log.info`, teste falha, restaurado). Suíte completa: 873 passed (3x
limpa).

#### §34.74.36 — INCIDENTE: desligar o servidor (Ctrl+C) perdia progresso de TODO MUNDO conectado, sem salvar nada (05/08/2026)

Usuário relatou: configurou a hotbar numa sessão, confirmou que salvou —
mas na sessão seguinte a configuração não estava lá. Suspeitou do
desligamento do servidor no meio.

**Causa raiz confirmada**: `server/main.py` não tinha NENHUM tratamento
de shutdown gracioso — grep por `signal`/`SIGTERM`/`atexit` em `server/`
não achou nada. `Ctrl+C` levanta `KeyboardInterrupt`, capturado só no
`if __name__ == "__main__":`, DEPOIS que `asyncio.run()` já fechou o
loop de eventos — tarde demais pra rodar qualquer save assíncrono. O
processo simplesmente morria. Resultado: **tudo que mudou desde o
último autosave (5 em 5 minutos) ou desde a última ação que dispara save
se perdia por completo** ao desligar — não só hotbar, qualquer estado de
QUALQUER player conectado (inventário, ouro, quest, posição).

**Fix**: `server/main.py::main()` — `except KeyboardInterrupt` movido pra
DENTRO do `async with websockets.serve(...)` (ainda com o loop vivo),
envolvendo o `await world.run()`. Ao capturar, chama `await mgr.
_autosave_all()` — o MESMO chokepoint do autosave periódico
(`server/session.py`, já usa `_persist_character`, que já pula sozinho
quem estiver dentro de progressão normalizada de BG — nenhum guard novo
precisou entrar aqui) — antes de deixar a exceção propagar (`raise`) pro
tratamento externo de sempre.

Teste: `tests/test_graceful_shutdown.py` — `WorldServer.run` trocado por
um fake que levanta `KeyboardInterrupt` na hora, `SessionManager.
_autosave_all` espionado, chama `main()` de verdade (porta 0 — SO escolhe
uma livre) e confirma que `_autosave_all` roda exatamente 1x antes da
exceção propagar. Prova diferencial confirmada (except trocado pra
`RuntimeError`, nunca bate com `KeyboardInterrupt`, teste falha,
restaurado). Suíte completa: 874 passed (3x limpa).

#### §34.74.37 — Estratégia de escala: índice de players por tick + elimina scan redundante de alvo (05/08/2026)

Usuário notou (log real, só ele jogando) `ai_bundles`/`bnd:map_1`
consistentemente em ~7-8ms/tick (70% do tick) e perguntou se isso
pioraria com mais players. Isso puxou uma discussão maior sobre
estratégia de escala (não documentada em detalhe aqui, ver histórico da
conversa) — como MMOs/MOBAs reais (WoW/LoL/Tibia) resolvem esse
problema: particionamento espacial como camada ÚNICA (nunca patch por
sistema), LOD por distância/prioridade, isolamento de partida por
processo (LoL/Dota: cada partida = processo isolado, nunca 1 servidor
grande aguentando N partidas), por que a IA de mob TEM que rodar no
servidor (cliente não é confiável — abriria brecha de trapaça, mesma
regra de sempre deste projeto: "servidor valida tudo"), e por que
paralelizar dentro de 1 tick em Python esbarra no GIL (threading comum
não ajuda CPU-bound; `multiprocessing` contorna mas exige reprojetar
estado compartilhado; Python 3.14 free-threading é real e oficialmente
suportado agora, mas exige migrar de versão + auditar dependências C —
fica pra depois). Desta discussão, decidiu-se implementar AGORA só os 2
achados **sem trade-off** (ganho puro, comportamento idêntico); uma
fila de prioridade/throttle (ideia do usuário, ganha mais mas troca
responsividade) fica **FASE 2**, ainda não implementada.

**Achado 1 — scan de alvo redundante**: `EnemyAISystem.update()`
(`engine/world_systems.py`) chamava `_select_target()` (2 scans: players
do mapa + outros combatentes) INCONDICIONALMENTE pra todo mob acordado
— mesmo um já `ATTACKING`/`CHASING` com alvo retido e válido. A
retenção só sobrescrevia o resultado DEPOIS, jogando o scan fora. Fix:
reordenado — a validação de retenção (mesma lógica de sempre) roda
PRIMEIRO; `_select_target` só é chamado quando ela falha (sem alvo, ou
alvo virou inválido). Resultado idêntico, zero mudança de
comportamento, só ORDEM.

**Achado 2 — scan de players sem cache, por mob**: sleep-check (mob
IDLE decidindo se continua dormindo) e `_select_target` faziam
`get_entities_with(...PlayerControlled...)` por MOB, todo tick, sem
cache — o sleep-check sozinho já rodava esse scan pra CADA um dos ~190
mobs do mapa aberto. `EnemyAbilitySystem`/`SpawnZoneSystem` faziam scans
parecidos (mais baratos — 1x por bundle, não por mob — mas cada um
reimplementava). Fix: índice canônico `players_by_map` (dict
map_file→lista de `(eid, Position, TileMovement, CombatStats)`),
construído 1x por tick em `WorldServer._tick()` (mesmo padrão já
validado nesta sessão pra `_combat_spatial_hash`, §34.74.30), injetado
via novo parâmetro `players_by_map=None` no `update()` dos 3 sistemas —
helper único `_players_on_map()` (`engine/world_systems.py`) resolve do
índice se fornecido, cai no scan de sempre se `None` (compat com
qualquer teste que já chama `.update()` direto sem esse argumento,
mesmo padrão de `spatial_hash=None` do Tower/MinionSystem).
`_MapBundle.proximity_systems` (novo set, mesmo padrão de
`ai_systems`) marca quais sistemas do bundle recebem o índice.

Testes: `tests/test_enemy_ai_perf.py` (3 casos, prova diferencial em
cada um): mob com alvo retido não rechama `_select_target` (mas volta a
chamar se o alvo morre); um "fantasma" (entidade real, SEM
`PlayerControlled` — nunca apareceria num scan de verdade) presente só
no índice injetado é adquirido como alvo, provando que o sistema
consome o índice em vez de escanear sozinho. Suíte completa: 877 passed
(3x limpa).

**Fase 2 (NÃO implementada, registrada como próximo passo)**: fila de
prioridade/throttle pra mobs IDLE que ainda estão PROCURANDO alvo (sem
retenção) — reavaliar a cada N ticks em vez de todo tick, priorizado
por tier (boss/elite/raro/normal) com "aging" pra nunca deixar um mob
starvado. Decisão pendente do usuário: tamanho de N (folga de
responsividade aceitável). Mob em combate ativo NUNCA deveria ser
afetado por esse throttle (sempre full-rate) — só a busca "ainda não
achei ninguém" é candidata a atraso.

#### §34.74.38 — Instrumentação de perf (RSS/GC/sub-sistema/mobs ativos) + Achado 3: `_select_target` rodava pra mob isolado a centenas de tiles de qualquer player (05/08/2026)

Continuação de §34.74.37. Usuário testou com 1 depois 2 players e pediu
análise do `server_perf.log` de novo — `ai_bundles`/`bnd:map_1` continuava
em 7-16ms mesmo depois do fix anterior. Descartar hipóteses antes de mexer
em código (pedido explícito do usuário, mesma régua de
`feedback_validate_before_suite_and_no_loop_guessing`):

- **Instrumentação adicionada em `server/world_server.py`** (nenhuma
  muda comportamento, só mede): RSS do processo por tick (`psutil`, já
  usado pra CPU%) reportado no resumo periódico (`rss avg=/peak=`);
  duração + tick# de cada `gc.collect()` manual (`run()`) logado em
  `server_perf.log`; sub-timer por sistema dentro do bundle
  (`sys:EnemyAISystem`, `sys:SpawnZoneSystem` etc. — reusa
  `_perf_mark`, nunca duplica leitura de `perf_counter()`); contador de
  mobs em `CHASING`/`ATTACKING`/`AGGRO_DELAY` por tick
  (`mobs_ativos avg=/peak=`, no resumo periódico E na linha "tick
  lento").
- **Hipótese GC/memória: descartada** — RSS fica estável (~55-62MB,
  platô), `gc.collect()` roda em ticks fixos (múltiplos de 300) que
  nunca coincidem com os ticks dos picos isolados de 75-190ms.
- **Hipótese "mobs acumulando em combate": descartada pelos próprios
  dados** — `mobs_ativos avg=0.0 peak=0` durante janelas inteiras com
  `sys:EnemyAISystem` custando 8-16ms. Ninguém em combate, custo alto
  mesmo assim.
- **Achado 3 (causa real)**: usuário observou que os ~190 mobs do mundo
  estão espalhados por várias zonas de spawn — confirmado lendo
  `maps/map_1_entities.json` (`spawn_zones`): 11 zonas em map_1 somando
  124 mobs, zonas a 400+ tiles de distância umas das outras (`y=40` até
  `y=490`). `EnemyAISystem.update()` chama `_select_target()`
  (`engine/world_systems.py`) pra TODO mob sem alvo retido,
  incondicionalmente — o sleep-check existente (`SLEEP_RADIUS_TILES=40`,
  linha ~2792) só roda DEPOIS do branch "sem alvo válido" já ter dado
  `continue` (linha ~2698), então NUNCA é alcançado por um mob que
  já não tinha alvo (o caso comum de um mob isolado, longe de
  qualquer player). Resultado: `_select_target` (2 scans + `is_hostile`/
  `CombatState` por candidato) roda todo tick pra praticamente todo mob
  IDLE do mapa, mesmo os isolados a centenas de tiles.
- **Fix**: `EnemyAISystem._any_candidate_in_range(mob_eid, mob_pos)`
  (`engine/world_systems.py`) — pré-filtro só de matemática de posição
  (sem `is_hostile()`/`get_component(CombatState)`, os 2 lookups mais
  caros por candidato), usando o MESMO raio que `_select_target` já usa
  de verdade (`AGGRO_RADIUS_TILES`, não `SLEEP_RADIUS_TILES` — usar um
  raio maior mudaria o resultado). Prova de segurança: `_select_target`
  nunca aceita candidato além desse raio (seu `best_dist` é inicializado
  com ele), então se NINGUÉM (player ou NPC/combatente — cobre os dois
  loops) está dentro dele, a chamada de verdade sempre devolveria -1;
  pular é matematicamente idêntico, nunca uma aproximação. Chamado em
  `update()` como `if not _retained and self._any_candidate_in_range(...)`.
- Testes: `tests/test_enemy_ai_perf.py::TestSelectTargetSkippedWhenNoCandidateInRange`
  (2 casos, prova diferencial): mob isolado a 5000 tiles não chama
  `_select_target`; player entrando no raio real de aquisição ainda
  chama e adquire normalmente (prova que o pré-filtro não quebra
  aquisição legítima). Setup usa `utils.snap_to_tile()` (nunca escrita
  direta de `current_tile_x/y`) — necessário porque o pré-filtro
  depende de `Position` (pixel) estar sincronizado com `TileMovement`,
  o que expôs o mesmo problema em 2 testes PRÉ-EXISTENTES de
  §34.74.37 (moviam só `TileMovement`, nunca importava antes porque
  nem retenção nem a chamada incondicional de `_select_target`
  dependiam de `Position` real) — corrigidos junto. Suíte completa: 879
  passed (3x limpa).
- **Iteração global filtrada por mapa (corrigida no mesmo dia, depois de
  medir o resíduo)**: usuário testou de novo e perguntou por que
  `ai_bundles` ainda ficava >6ms em alguns momentos mesmo depois do
  Achado 3 — log confirmou que não era mais chamada desperdiçada
  (`mobs_ativos=0` mas custo ainda presente), e sim o PISO de iterar a
  população inteira todo tick. Causa: `EnemyAISystem.update()` usava
  `get_entities_with(Position, AIControlled, InitialPosition,
  DetectionRadius, TileMovement, CombatStats)` SEM filtro de mapa na
  query — itera TODOS os mobs do MUNDO (todos os 3 mapas), descartando
  os de outro mapa 1 a 1 via `get_component(MapLocation)` + comparação,
  dentro do próprio loop principal. Com múltiplos mapas ativos ao mesmo
  tempo, cada bundle repetia esse scan global inteiro (mesma classe de
  bug já corrigida pra players em §34.74.37, agora pra mobs). Fix:
  índice canônico `mobs_by_map` (dict map_file→lista de `(eid, Position,
  AIControlled, InitialPosition, DetectionRadius, TileMovement,
  CombatStats)`), construído 1x por tick em `WorldServer._tick()`
  (mesmo bloco que já constrói `_players_by_map`), consumido via helper
  `_mobs_on_map()` (`engine/world_systems.py`, mesmo padrão de
  `_players_on_map`) — cai no scan de sempre se `mobs_by_map=None`
  (compat com teste que chama `.update()` direto). `EnemyAbilitySystem`/
  `SpawnZoneSystem` ganharam o parâmetro `mobs_by_map=None` só por
  uniformidade de dispatch (mesmo grupo `proximity_systems`, chamado com
  os 2 índices igual em todo mundo) — nenhum dos dois usa o índice de
  mobs ainda, não iteram mob por conta própria.
  Teste: `tests/test_enemy_ai_perf.py::TestMobsByMapIndexIsActuallyUsed`
  — mob "fantasma" SEM `InitialPosition`/`DetectionRadius` (2 dos 6
  componentes exigidos pelo scan real — nunca apareceria nele) presente
  só no índice injetado precisa ser processado e adquirir o player como
  alvo; prova diferencial confirmada (índice desligado → alvo continua
  -1). Suíte completa: 880 passed (3x limpa).

#### §34.74.39 — `_get_occupied_tiles` reaproveitado (Camada 1) + fragilidade de teste corrigida (05/08/2026)

Usuário testou de novo (log real) e perguntou por que `ai_bundles` ainda
picava >100ms em alguns ticks isolados (ex: tick#3910, 221ms — coincidindo
com queda de 191→180 mobs, leva de leash/perda de alvo simultânea).

**Causa**: `EnemyAISystem._get_occupied_tiles()` (`engine/world_systems.py`)
faz `get_entities_with(TileMovement)` SEM filtro de mapa — escaneia TODAS
as entidades com `TileMovement` do MUNDO TODO, descartando por mapa depois.
Chamada 1x por tick no topo de `update()` (`all_occupied_tiles`), mas
TAMBÉM re-chamada **por mob**, em 3 branches (RETURNING×2, kite) toda vez
que aquele mob precisa recalcular caminho. Com vários mobs perdendo o alvo
no mesmo tick, cada um pagava um rescan global inteiro.

Usuário sugeriu também restringir a busca a uma área local (ex: 15x15 ao
redor do mob) via `SpatialHash` (`engine/utils.py:63`, já usado em outros
lugares) — ideia validada mas adiada (Camada 2, troca correção rara por
performance, NÃO implementada — decisão pendente).

**Fix (Camada 1, zero trade-off)**: as 3 chamadas por-mob passam a
reaproveitar `all_occupied_tiles` (já computado 1x por tick), subtraindo
só o próprio tile do mob via novo helper `EnemyAISystem._tile_of()`
(mesmo critério de exclusão que `except_entity_id` fazia antes). Resultado
idêntico — nunca mais rescan global por mob.
Teste: `tests/test_enemy_ai_perf.py::TestReturningReusesOccupiedTilesInsteadOfRescanning`
— mob isolado forçado a recalcular retorno; spy em `_get_occupied_tiles`
confirma 1 chamada total (só a do topo), prova diferencial confirmada.

**Regressão descoberta durante validação — NÃO era bug do fix acima**:
suíte completa passou a falhar em `test_server.py::TestMobMovement::
test_mob_moves_toward_player` (determinístico, mesmo teste nas 2
tentativas). Investigação por trace (6 rounds, instrumentação temporária
removida depois): `_select_target()` encontrava o player corretamente
(hostil, vivo, dentro do raio), `target_eid` era persistido — mas a
transição `IDLE→AGGRO_DELAY` (`engine/world_systems.py` ~linha 3244) tem
um segundo gate, linha de visão (`_has_line_of_sight`), que bloqueava a
transição. Causa raiz: o teste usava `first_mob()` (primeiro mob hostil
que aparecer) + offset fixo de +3 tiles em X a partir da posição NATIVA
do mob — e o `random` global do Python não é resetado por teste, então
`first_mob()` pode devolver mobs diferentes dependendo de quantos testes
(e quantas chamadas a `random`) rodaram antes no mesmo processo. Um mob
cuja posição nativa tinha parede 3 tiles a leste quebrava o teste mesmo
com tudo mais (hostilidade/HP/distância) correto — bug pré-existente,
só nunca exposto porque a ordem de execução nunca tinha empurrado
`first_mob()` pra esse mob específico antes.

Também descoberto no processo: o próprio tile de spawn de teste de
`TestMobMovement` (130,374 em map_1) tem uma parede 1 tile a LESTE
(131,374, sólido) — bloqueando LOS pra qualquer offset_x positivo nesse
spawn especificamente.

**Fix do teste**: `test_mob_moves_toward_player` agora teleporta o MOB
pra perto do player (`tests/helpers.py::teleport_mob_to_player`, offset_y
em vez de offset_x — sul é aberto e com LOS livre nesse spawn) em vez de
mover o player pra posição nativa do mob. `teleport_mob_to_player` ganhou
parâmetro novo `offset_y: int = 0` (default preserva comportamento de
todo chamador existente). Decisão do usuário: corrigir só este teste
específico agora — resetar `random.seed()` por teste (fix sistêmico pra
essa CLASSE de fragilidade, qualquer teste futuro pode reordenar RNG e
expor outro caso parecido) fica registrado como possível trabalho futuro,
não decidido ainda.

Suíte completa: 881 passed (limpa, mesma ordem que antes expunha a falha).

#### §34.74.40 — Achado 5: mob IDLE longe de todo player continuava iterado (mesmo depois do §34.74.38/39) (05/08/2026)

Usuário perguntou (com uma meta explícita: IA de mob/NPC deveria cair pra
~1ms, picos ~2ms) se, mesmo parado numa área específica de um mapa
gigante, o sistema ainda calculava IA do mapa inteiro todo tick. Resposta:
sim — os achados 1-4 (§34.74.37/38/39) reduziram o CUSTO por mob distante
(pulava a parte cara), mas o mob continuava sendo ITERADO: passava por
checagem de morto, retenção, pré-filtro, etc., antes do sleep-check
(`SLEEP_RADIUS_TILES=40`, dentro do loop) finalmente descartar ele. Pra
~130 mobs em map_1, isso é ~130 iterações completas todo tick mesmo com
só uns 10-20 perto do player.

Usuário também propôs jitter no `aggro_delay` (hoje fixo em 1.0s — só o
`path_recalc_timer` pós-CHASING já tinha jitter) pra espalhar pathfind de
agros em massa — registrado como possível ajuste complementar, MENOS
urgente depois deste fix (menos mobs simultâneos = menos agro em massa).
Também sugeriu investigar o algoritmo em si — decidido reprofilar DEPOIS
deste fix, com dado real, em vez de adivinhar.

**Fix**: `EnemyAISystem._active_mobs_this_tick()` (`engine/world_systems.py`,
método novo, chamado no lugar de `_mobs_on_map` direto no loop principal)
— filtra ANTES do loop: mob NÃO-IDLE sempre entra (tem trabalho
pendente — leash/retorno); mob IDLE só entra se dentro de
`SLEEP_RADIUS_TILES` de algum player OU se `_any_candidate_in_range`
(mesmo pré-filtro do Achado 3, já cobre NPC/combatente) acha candidato
dentro do raio real de aquisição. Resultado idêntico ao sleep-check de
sempre — só aplicado ANTES do corpo pesado, não durante.
**NÃO usa `SpatialHash`** — descartado de propósito na discussão: com
poucos players (1-4), comparar cada mob contra a lista inteira de
players já é barato; o ganho vem de fazer isso ANTES do resto da lógica,
não de acelerar a comparação em si.

**Regressão real encontrada e corrigida durante a validação**: a
primeira versão só verificava distância a PLAYERS, quebrando 3 testes de
`tests/test_faction.py::TestMultiTargetCombat` (mob-vs-NPC longe de todo
player, propositalmente — Sistema de Facções Fase 5). Causa: no código
ANTES deste fix, o sleep-check "funcionava" pra esse caso só porque
`_select_target`/retenção rodavam ANTES dele, e a variável de distância
usada pelo sleep-check (`chebyshev_dist_to_player`, nome enganoso) na
verdade media distância ao ALVO JÁ ADQUIRIDO (podia ser um NPC) — nunca
literalmente "distância a um player". Um pré-filtro que olhasse só pra
`players_this_map_cache` quebra esse caso escondido. Fix: adicionado o
mesmo `_any_candidate_in_range` (cobre NPC/combatente) como segunda
condição pro mob IDLE entrar na lista.

Teste: `tests/test_enemy_ai_perf.py::TestIdleMobFarFromPlayerNeverEntersMainLoop`
— verifica DIRETO a lista devolvida por `_active_mobs_this_tick` (não
conta chamadas de função auxiliar — `_any_candidate_in_range` é usada
tanto pelo pré-filtro quanto pelo corpo do loop, contar chamadas não
distingue "filtrado antes" de "entrou e foi rejeitado dentro"). Mob IDLE
isolado (sem player nem NPC por perto) não aparece na lista; mob CHASING
isolado continua aparecendo (trabalho pendente). Prova diferencial
confirmada (fix desligado → todos os ~130 mobs aparecem, incluindo o
isolado). Suíte completa: 883 passed (3x limpa, incluindo os 3 testes de
`TestMultiTargetCombat` que quebraram e foram corrigidos).

#### §34.74.41 — `SLEEP_RADIUS_TILES` 40→20 (teste do usuário) + Fase 2: throttle de reavaliação por tier (05/08/2026)

Usuário testou o Achado 5 com 1 player e o ganho foi menor que o
esperado (`sys:EnemyAISystem` só caiu de ~4.2-5.4ms pra ~3.8-4.2ms) —
hipótese: zonas de spawn próximas/densas (cluster ao sul de map_1,
y=340-490, raios 35-38 tiles) mantêm muitos mobs "no raio" mesmo com o
filtro funcionando certo. Usuário propôs testar `SLEEP_RADIUS_TILES`
(`EnemyAISystem`) reduzido de 40 pra **20** — **mudança de
comportamento real, não só perf** (mob passa a "acordar" mais perto do
player). Validado pelo usuário em teste manual antes de seguir.

**Regressão real encontrada e corrigida**: `tests/test_service_npcs.py::
TestServiceNpcVoltaProSpawnExato::test_npc_deslocado_volta_pro_tile_exato`
quebrou — NPC deslocado (IDLE, nunca esteve em combate) a 22 tiles do
player não se autocorrigia mais (achado 5 não processa mob IDLE fora do
raio, sem exceção pra "correção de posição sem combate"). Fix: aproximar
o player do NPC no teste (`spawn_player` movido pra dentro de 20 tiles),
não reverter o raio — comportamento aceito pelo usuário como esperado.

**Usuário perguntou se um mob NÃO-IDLE (RETURNING/CHASING) fica de fora
do raio depois de perder o alvo perto do player**: resposta confirmada
pelo código — não, mob não-IDLE SEMPRE entra no filtro do Achado 5,
independente de distância (linha `if ai_control.state != "IDLE": ...
continue`) — o fluxo real de combate→retorno nunca passa por um estado
IDLE-mas-ainda-longe (vai direto de CHASING/ATTACKING pra RETURNING, só
vira IDLE de novo quando já está perto de casa). O raio só afeta mob que
NUNCA teve trabalho pendente.

**Usuário testou com 2 players**: custo dobrou de verdade (`sys:
EnemyAISystem` ~4-6ms→~7-9ms, tick médio ~9-14ms→~16-20ms,
`mobs_no_filtro_ai` — contador novo — de ~12-18 pra ~14-18 em janelas
comparáveis). Confirmado: não é mais desperdício (já eliminado nos
achados 1-5), é trabalho genuíno — cada player cria sua própria "bolha"
de raio, mais players = mais bolhas = mais mobs "acordados"
simultaneamente. Decisão: avançar pra Fase 2 (registrada desde
§34.74.37) — throttle de reavaliação, trocando um pouco de
responsividade por economia real.

**Fase 2 implementada** (plano em Plan Mode, aprovado pelo usuário):
- **Escopo**: só mob `state == "IDLE"` é candidato a throttle — mob
  procurando alvo, ainda não achou ninguém. `CHASING`/`ATTACKING`/
  `AGGRO_DELAY`/`KITING`/`RETURNING`/`BLOCKED_BY_PLAYER` NUNCA são
  throttlados (trabalho pendente real, sempre full-rate).
- **Prioridade por tier**: reaproveita `EnemyTier` (`engine/
  components.py`, já atribuído a todo mob/NPC de combate via
  `create_enemy`/`create_combat_npc` → `_build_combat_entity`) — não
  criou conceito novo de prioridade.
- **Intervalos** (`EnemyAISystem._THROTTLE_INTERVAL_BY_TIER`, ticks
  entre reavaliações, TICK_RATE=30, perfil "Recomendado" escolhido pelo
  usuário entre 3 opções apresentadas): boss=1 (nunca throttla), elite=3
  (~100ms), rare=6 (~200ms), normal=12 (~400ms). Atraso máximo pra um
  mob IDLE notar um player pela primeira vez — imperceptível já que
  `aggro_delay` (1s) já existe DEPOIS disso.
- **Mecanismo — "último tick checado", não fila/round-robin**: novo
  campo `AIControlled._ai_throttle_last_check: int = -1` (default -1 =
  nunca checado). Regra: mob elegível só entra na lista se `tick_count -
  _ai_throttle_last_check >= intervalo_do_tier`; ao entrar, atualiza o
  campo. Com -1 como default, a PRIMEIRA vez que um mob fica elegível
  ele SEMPRE entra imediatamente — descartou `(tick_count + eid) %
  intervalo` de propósito (um mob recém-elegível podia esperar até
  `intervalo` ticks pro primeiro check, quebrando testes curtos como
  `test_faction.py::TestMultiTargetCombat` que rodam só 3 ticks, e
  atrasando a reação real de um mob que acabou de entrar no raio).
- Aplicado dentro de `EnemyAISystem._active_mobs_this_tick()` (mesmo
  ponto do Achado 5) — `tick_count` injetado via novo parâmetro em
  `update()`, mesmo padrão de `players_by_map`/`mobs_by_map`
  (`WorldServer._tick()` repassa `self.tick_count`).
  `EnemyAbilitySystem`/`SpawnZoneSystem` ganharam o parâmetro só por
  uniformidade de dispatch (aceitam e ignoram).

Testes: `tests/test_enemy_ai_perf.py::TestThrottleOfIdleMobReevaluation`
(5 casos) — mob recém-elegível entra no 1º tick; não entra de novo antes
do intervalo; entra de novo exatamente quando o intervalo completa; boss
nunca throttla; mob não-IDLE nunca é afetado mesmo com
`_ai_throttle_last_check` recente. Prova diferencial confirmada (throttle
desligado → mob reaparece antes do intervalo, teste que verifica ausência
falha). Suíte completa: 888 passed (3x limpa, incluindo o fix do teste de
NPC de serviço).

#### §34.74.42 — Pesquisa de escala (10 players/PC com minions), grid espacial descartado, Fase 3: throttle de recálculo em CHASING (06/08/2026)

Usuário pediu pesquisa profunda sobre como jogos consolidados lidam com
escala de IA/combate com muitos players concorrentes, preocupado com "10
players já crashariam o jogo, inclusive dentro de uma PC onde a zona é
pequena e terá combates entre players com minions junto". Dois agentes de
pesquisa em paralelo (mundo aberto/MMO — WoW/TrinityCore, EVE Online; e
MOBA/instância densa — LoL/Dota, AWS GameLift) investigaram padrões da
indústria.

**Item 1 da síntese (grid espacial) CONFERIDO e DESCARTADO**: o código já
rejeitava essa ideia com motivo documentado (docstring de
`_active_mobs_this_tick`, §34.74.40) — "com poucos players... comparar
cada mob contra essas listas pequenas já é barato". Conferência contra a
arquitetura real (não só o comentário) confirmou o motivo:
`_players_by_map`/`_mobs_by_map` (`server/world_server.py:3950-3974`) já
são construídos 1x por tick E já vêm particionados por MAPA (bundle) —
não é scan global. Dentro de cada bundle, o loop de elegibilidade só
compara mobs DAQUELE mapa contra players DAQUELE mapa (1-10 no pior
caso). O particionamento que um grid faria por célula já existe, na
granularidade de mapa inteiro — suficiente pro tamanho real do mundo
aqui. Sharding/layering (WoW layering, GW2 megaserver) e combate
agregado/estatístico (wargaming) também descartados pela pesquisa —
multiplicam custo ou são do gênero errado, não resolvem o problema.
Multiprocessing por mapa (modelo real do EVE Online — 1 processo por
solar-system) fica registrado como resposta de longo prazo SE as fases
mais simples não bastarem, não necessário agora — a arquitetura por
`map_file` já é a base natural pra isso no futuro; ressalva: Windows só
tem `spawn` (sem `fork`), workers ~20x mais lentos pra inicializar
(benchmark citado pela pesquisa), o que encarece esse caminho pro
alvo de deploy atual.

**Causa real do crescimento por player**: não é O(mobs×players) caro —
é que cada player em zona DIFERENTE desperta um conjunto DIFERENTE de
mobs, e cada mob que entra no corpo pesado do loop principal é trabalho
genuíno (mesma conclusão já registrada em §34.74.41). As Fases 1-2
atacaram a fatia "mob procurando alvo, sem achar". Fase 3 ataca a fatia
"mob JÁ em combate, perseguindo" — que as Fases 1-2 deliberadamente não
tocaram.

**Achado concreto**: dentro do bloco de perseguição, o gatilho "alvo
mudou de tile" de `should_recalculate_path` (busca de tile de ataque —
loop O((attack_range+1)²) — mais chamada A* via `_find_path_budgeted`)
disparava recálculo IMEDIATO toda vez que o alvo mudava de tile —
ignorando o cooldown normal de `path_recalc_timer` (0.8s) quase todo
tick enquanto o alvo está em movimento. Com muitos mobs perseguindo
simultaneamente (cenário de PC — vários minions convergindo pro mesmo
player), isso rodava TODO tick pra cada um, não 1x a cada 800ms como o
timer normal sugere. O orçamento global de A* por tick
(`MAX_PATHFINDS_PER_FRAME=10`) já protegia contra travamento, mas a
busca de tile de ataque (o loop duplo ANTES da chamada A*) não era
coberta por esse orçamento.

**Fase 3 implementada** (plano em Plan Mode, aprovado pelo usuário —
usuário escolheu "LOD dentro de combate" entre 4 frentes propostas pela
síntese da pesquisa):
- **Escopo do throttle**: SÓ a condição "alvo mudou de tile" de
  `should_recalculate_path`. As outras três (`path is None`, `path`
  vazio, `is_blocked`) continuam imediatas — recuperação de
  erro/bloqueio, nunca throttladas.
- **Intervalos** (`EnemyAISystem._CHASE_RECALC_MIN_TICKS_BY_TIER`, ticks,
  TICK_RATE=30) — mais agressivos que os do throttle de IDLE (§34.74.41)
  porque aqui o mob já está em combate ativo: boss=1, elite=1 (nunca
  throttlam — poucos por mapa, resposta sempre nítida), rare=2 (~66ms),
  normal=4 (~130ms). Entre recálculos throttlados o mob continua andando
  pelo path já calculado — path fica no máximo ~130ms desatualizado
  (tier normal), autocorrigido no próximo recálculo permitido.
- **Mecanismo**: mesmo padrão "último tick" do §34.74.41 — novo campo
  `AIControlled._chase_recalc_last_tick: int = -1`, stamp atualizado
  junto de `last_known_player_tile` (independente de qual dos 4
  gatilhos disparou o recálculo).
- Arquivos: `engine/components.py::AIControlled` (novo campo),
  `engine/world_systems.py::EnemyAISystem` (nova constante + gate em
  `should_recalculate_path`, linha ~3288).

Testes: `tests/test_enemy_ai_perf.py::TestThrottleOfChaseRecalcOnTargetMove`
(3 casos) — tier normal respeita o intervalo mínimo entre recálculos
forçados por mudança de tile; boss nunca throttla (replaneja todo tick);
path vazio força recálculo imediato mesmo dentro da janela de throttle
(prova que os outros 3 gatilhos continuam intocados). Depuração real
durante a escrita do teste: a "desistência de perseguir" genuína do jogo
(`dist_to_player_pixels > detect_radius.radius`, ~linha 3494) disparava
sozinha porque o teste afastava o alvo rápido demais sem o mob se mover
— não é bug do throttle, é o teste precisando de `DetectionRadius`
generoso pra isolar só o mecanismo sob prova (mob fica parado de
propósito nesses testes isolados, cenário que o jogo real nunca produz
sozinho). Prova diferencial confirmada (throttle desligado → gap de 1
tick entre recálculos, teste que exige gap≥intervalo falha). Suíte
completa: 891 passed (3x limpa).

**Verificação pendente**: pedir pro usuário testar um cenário de combate
denso (BG com vários minions perseguindo/atacando simultaneamente, ou
grupo de mobs no mundo aberto) e comparar `sys:EnemyAISystem` no
`server_perf.log` — os logs testados até agora (2 players, BG com pouco
movimento simultâneo) não estressaram esse cenário especificamente
(Fase 3 só afeta mob CHASING com alvo em movimento, não ticks
parados/IDLE).

#### §34.74.43 — Fase 4: throttle de recálculo de destino em FIGHTING (MinionSystem) (06/08/2026)

Continuação direta da auditoria do `MinionSystem` pedida pelo usuário
depois da Fase 3. Achado: `MinionSystem` (`engine/world_systems.py:1777`)
é bem mais otimizado do que a pesquisa fazia supor — NÃO é o "FSM
ingênuo caro" temido (avanço pela lane é O(1)/tick sem pathfinding,
aquisição de alvo já usa `spatial_hash`, orçamento de A* já é por mapa).
Mas achou o MESMO padrão de problema da Fase 3, num sistema diferente,
sem throttle nenhum: dentro de `FIGHTING`, o destino passado pra
`_walk_toward` era a tile ATUAL do alvo, recalculada fresh todo tick —
`_walk_toward` descarta `current_path`/`path_recalc_timer` IMEDIATO toda
vez que o destino muda, ignorando o backoff normal (0.8s) quase todo
tick com o alvo em movimento. Com uma wave inteira `FIGHTING`
simultaneamente (cenário de PC que motivou a pesquisa), isso dispara
`_get_occupied_tiles` (scan do mapa) + tentativa de A* todo tick que
qualquer alvo muda de tile.

**Minions já têm `EnemyTier`**: `create_minion`
(`engine/entity_factory.py:778`) já anexa `EnemyTier(tier=mdef["tier"])`
— `MINION_TABLE` já usa `"normal"`/`"rare"` — reaproveitado sem criar
conceito novo.

**Mecanismo — diferente da Fase 3 no PONTO de aplicação**: `_walk_toward`
é compartilhado por ADVANCING (mira ponto fixo da rota, já estável) e
FIGHTING (o problema real) — throttlar DENTRO dela exigiria parâmetro
novo pra distinguir os dois casos. Em vez disso, o throttle decide, no
branch FIGHTING de `update()`, qual `dest` passar: se o alvo mudou de
tile mas ainda não é a vez do tier, passa o MESMO `dest` de antes
(`minion.path_dest`) em vez do novo — `_walk_toward` vê "destino igual",
não reseta nada. `_walk_toward` em si não mudou.

**Intervalos**: constante PRÓPRIA de `MinionSystem`
(`_TARGET_RECALC_MIN_TICKS_BY_TIER`, mesmos valores da Fase 3 mas sem
acoplar as duas classes — sistemas deliberadamente independentes):
boss=1, elite=1 (nunca throttlam), rare=2 (~66ms), normal=4 (~130ms).

**Campo novo**: `Minion._target_recalc_last_tick: int = -1`
(`engine/components.py`, mesmo padrão -1-sempre-passa-na-primeira-vez).
`MinionSystem.update()` ganhou parâmetro `tick_count: int = 0`;
`WorldServer._tick()` (linha ~4491) passa `tick_count=self.tick_count`.

**Bug real pego pela suíte, corrigido antes de fechar**: primeira versão
carimbava `_target_recalc_last_tick` sempre que DECIDIA usar o destino
novo — mas `_walk_toward` tem seu PRÓPRIO early-return (`if
tm.is_moving: return`), ANTES de setar `path_dest` — se o carimbo
acontecesse num tick em que o minion ainda estava em movimento,
`path_dest` continuava `None`, e o PRÓXIMO tick throttlado tentava
reusar `None` como destino → `TypeError: 'NoneType' object is not
subscriptable`. Pego pela suíte completa (`TestMinionWaveSpawning`,
cenário real de wave), não pelos testes isolados do throttle (que não
exercitavam o `is_moving=True` no momento exato do carimbo). Fix:
`_due` força `True` também quando `minion.path_dest is None` (nunca
reusa um destino que na verdade nunca foi setado de verdade).

Testes: `tests/test_minions.py::TestThrottleOfMinionFightingRecalcOnTargetMove`
(3 casos, mundo headless 40×40 aberto via `_make_world_with_services()` —
sem as complicações de terreno real da Fase 3) — tier normal respeita o
intervalo mínimo (alvo movido pra uma tile NOVA a cada tick, nunca
repetida na janela — um padrão de só 2 tiles alternadas pode "voltar"
pro destino congelado e mascarar o throttle, achado real escrevendo o
teste); boss nunca throttla; alvo parado não precisa de throttle (destino
já é sempre o mesmo). Prova diferencial confirmada. Suíte completa: 894
passed (3x limpa).

**Verificação pendente**: mesma do §34.74.42 — pedir pro usuário testar
cenário de PC com minions em combate denso e comparar
`sys:minion_system`/`bnd:moba_battleground` no `server_perf.log`
antes/depois; nenhum log real analisado nesta sessão teve minions em
FIGHTING simultâneo suficiente pra estressar isso.

#### §34.74.44 — Fase 5: yield garantido pro asyncio sob sobrecarga sustentada + observabilidade de estado degradado (06/08/2026)

Item do roteiro da pesquisa: "orçamento de tempo por tick + degradação
graciosa". Investigação encontrou um problema mais concreto e mais sério
do que a ideia genérica de TiDi (EVE Online) da pesquisa, e mudou o
escopo real do fix (aprovado pelo usuário via AskUserQuestion).

**Achado real**: `WorldServer.run()` (`server/world_server.py:3780`) só
chamava `await asyncio.sleep()` no branch `else` (quando NÃO está
atrasado). Se `_tick()` demorar consistentemente mais que
`TICK_INTERVAL` (33ms) — sobrecarga SUSTENTADA, não um pico isolado —, o
branch que roda `_tick()` é sempre verdadeiro e o loop NUNCA cai no
`else`, ou seja, nunca devolve controle ao event loop do asyncio.
Nenhuma mensagem de WebSocket é processada, nenhuma conexão nova é
aceita — o processo continua vivo consumindo CPU, mas pra quem está
conectado o servidor **trava** (hang de rede — a causa real mais
provável do "crashar com muitos players" que motivou toda a pesquisa,
não falta de orçamento de CPU em si). O reset de `next_tick` (`if
time.perf_counter() - next_tick > TICK_INTERVAL: next_tick =
time.perf_counter()`) já evita uma fila de catch-up crescendo sem
limite — o risco real não é backlog infinito, é especificamente a falta
de ponto de yield.

**Escopo descartado**: mudar `TICK_RATE` dinamicamente de verdade (a
ideia original de degradação graciosa tipo TiDi) tem acoplamento real
com lag compensation — `server/skill_processor.py:270` converte ms→ticks
via `int(_lag_ms / (1000.0/TICK_RATE))`, assumindo `TICK_RATE` fixo.
Mudar a cadência real sem atualizar essa conta quebraria lag comp
silenciosamente. Descartado por ora — não necessário pro problema real
(o hang), que este fix já resolve sem tocar em `TICK_RATE`/protocolo.

**Fix 1 — yield garantido**: `run()` teve seu corpo de uma iteração
extraído pro método `_run_tick_or_sleep(next_tick) -> next_tick`
(testável isoladamente — `while self.running` direto não dá, roda pra
sempre). `await asyncio.sleep(0)` INCONDICIONAL adicionado ao final do
branch que roda `_tick()` — garante que o event loop sempre tem chance
de processar I/O pendente mesmo com `_tick()` consistentemente lenta,
tick após tick. Estado do `gc.collect()` periódico (`_gc_ticks`/
`_GC_EVERY`, antes locais de `run()`) virou campo de instância
(`_gc_ticks_since_collect`/`_GC_EVERY_TICKS`) já que cada iteração agora
é uma chamada própria, sem estado de loop sobrevivendo entre chamadas.

**Fix 2 — observabilidade de estado degradado**: novo método
`_update_overbudget_streak(tick_ms)`, chamado de dentro de `_tick()`
junto do bloco existente de "tick lento" (que já loga cada INCIDENTE
isolado). Conta ticks CONSECUTIVOS acima do budget — diferente de
incidente isolado, é sobrecarga SUSTENTADA de verdade. Ao cruzar
`_PERF_DEGRADED_STREAK_THRESHOLD=10` (~0.33s a 30 ticks/s — nenhum dos
logs reais analisados nesta sessão teve ticks lentos CONSECUTIVOS, só
incidentes isolados, então não deveria disparar falso-positivo), loga
UMA VEZ "SERVIDOR DEGRADADO" e seta `_perf_degraded=True`; ao voltar pro
budget com a flag ligada, loga "recuperou" com a duração do episódio.
Só logging — nenhuma mudança de comportamento/timing do tick em si.

Testes: `tests/test_world_server_loop.py` (4 casos) —
`TestRunLoopYieldsUnderSustainedOverload` prova que `asyncio.sleep()` é
chamado em TODA iteração mesmo com `next_tick` sempre no passado
(sobrecarga sustentada simulada); `TestOverbudgetStreakDetection` prova
o threshold exato, reset/recuperação, e que incidente isolado (1 tick
acima, depois normal) nunca ativa o estado degradado. Prova diferencial
confirmada nos dois mecanismos (yield removido → só 3/5 chamadas de
sleep; gate do threshold desligado → os 2 testes de streak falham).
Suíte completa: 898 passed (3x limpa).

**Verificação pendente**: não há como reproduzir sobrecarga sustentada
real num teste rápido — a prova do yield é estrutural (garantido por
construção). O log de "SERVIDOR DEGRADADO"/"recuperou" fica disponível
pra próxima vez que o usuário rodar um teste real com carga alta (mais
players/BG densa): se aparecer, confirma que a detecção funciona em
condição real; se nunca aparecer mesmo sob carga alta, é sinal de que o
servidor não está entrando nesse estado (bom sinal).

#### §34.74.45 — Último item do roteiro da pesquisa: "bucket rotation" investigado, achado redirecionado (06/08/2026)

Fecha o roteiro de 4 itens da pesquisa de escala (item 1, grid espacial,
já tinha sido descartado no §34.74.42). Investigação ANTES de
implementar (mesmo padrão do resto do capítulo) encontrou que o cenário
que "bucket rotation por ID" resolveria já está coberto:

- `_walk_toward` (`engine/world_systems.py:1990`, usado por ADVANCING e
  FIGHTING) já respeita `MinionSystem.MAX_PATHFINDS_PER_FRAME=15` — um
  teto DURO por mapa/tick pra quantos minions conseguem escanear tiles
  ocupados + rodar A* no mesmo tick, independente de quantos estejam
  "devidos" simultaneamente (já provado por
  `tests/test_minions.py::test_walk_toward_respeita_orcamento_de_pathfind_por_tick`).
  Combinado com o throttle por tier da Fase 4 (§34.74.43, que já reduz
  QUANTOS minions ficam devidos ao mesmo tempo), a parte cara (scan +
  A*) já tem teto duro — bucket rotation por cima seria redundante.

**O que NÃO está coberto** (achado real, mas descoberto ser questão de
CONTEÚDO, não de arquitetura): `MinionSystem.update()` itera todo
`Minion` do JOGO INTEIRO todo tick, sem filtro de mapa — custo O(N) do
total acumulado, individualmente barato mas cresce com a população.
Log real de teste solo (06/08/2026) mostrou `mobs=` subindo de 191→248
ao longo de ~9 ciclos de wave (`wave_interval_s` padrão 45s) sem nunca
cair — `_tick_minion_waves` (`server/world_server.py:1178`) dispara no
relógio, incondicional, nunca checa quantos minions da wave anterior
ainda estão vivos antes de criar mais. Achado que `_MINION_WAVE_COMPOSITION`
(linha 1146) já foi reduzido uma vez por esse motivo exato ("7→4 por
lane... pra baixar a carga de combate quando 2 waves se cruzam no meio
da lane", comentário de 04/08/2026) — ou seja, área já ativamente
gerenciada pelo usuário, não uma descoberta nova.

**Decisão do usuário**: não é claramente um bug de escala (custo real
chegou a ~9ms de 33ms de budget nesse teste, longe do limite) — é uma
decisão de design (teto de minions vivos por lane? wave pula ou atrasa?
comportamento real de MOBA costuma resolver isso via torre/base limpando
o acúmulo). Registrado aqui como observação, SEM implementação — fica
pra quando/se o usuário quiser ajustar o design de wave. Fecha o roteiro
da pesquisa de escala (§34.74.37 até aqui) sem trabalho pendente.

#### §34.74.46 — Fase 6: gate de morte pra cura (poção usada morto + corpo reagroando mob) (06/08/2026)

Primeiro de 3 bugs reportados numa sessão de playtest com um segundo
tester na BG (lista completa: morte, skills contra minion, barra de
ações — investigados em paralelo por 3 agentes de background antes de
qualquer código, mesma disciplina do capítulo 34.74). Prioridade
"mais grave primeiro", escolhida pelo usuário.

**Achado**: o projeto já tinha um choke-point único pra **dano** em
quem já morreu — `apply_damage_core` (`engine/core_systems.py:97-99`):
`if not cs or cs.current_hp <= 0: return "blocked_dead"`. Não existia o
equivalente pro lado da **cura**. Dois pontos aplicavam HP direto sem
checar morte:
- `apply_consumable` (`server/world_server.py:3087`) — só rejeitava se
  HP já estava no MÁXIMO, nunca checava `current_hp <= 0`. Resultado:
  poção usada no instante da morte era consumida (item perdido) sem
  curar nada.
- Loop de `ActiveRegen`/`ActiveManaRegen` (HoT de consumível,
  `world_server.py:4200-4260`) — aplicava cura por tick sem esse check.

**Por que "recuperou só um pouco de vida" E agroou mob**:
`_handle_player_death` (`server/respawn_system.py:53`) JÁ remove
`ActiveRegen` na morte — mas isso roda DEPOIS do loop de tick de
`ActiveRegen` no MESMO tick (morte processada num sweep mais tarde). Se
o player tinha HoT ativo no exato tick em que morreu, esse HoT aplicava
1 tick de cura antes da limpeza rodar — daí "só um pouco" (não full
heal). E como TODO gate de aggro de mob (`_select_target`,
`_any_candidate_in_range`, `_acquire_target` de minion) checa só
`current_hp > 0` (nunca um flag de morte separado), o corpo com HP>0
por 1 tick virava alvo válido de novo.

**Fix** — mesmo invariante que `apply_damage_core` já usa
(`current_hp <= 0`), aplicado simetricamente do lado da cura, sem
introduzir `GhostState.is_dead` (que só é setado DEPOIS, no sweep de
morte — usar esse flag manteria a mesma janela de atraso):
- `apply_consumable` — novo gate `if cs.current_hp <= 0: _reject("dead");
  return`, na mesma posição relativa de `apply_damage_core` (morte
  checada ANTES de qualquer outro bloqueio). Usa o `_reject()` já
  existente — cliente sempre recebe resposta, nunca silêncio (contrato
  já documentado na própria função).
- Loop de `ActiveRegen`/`ActiveManaRegen` — `if current_hp <= 0:
  continue` logo após buscar `CombatStats`, ANTES de tocar
  `tick_timer`/aplicar cura. PAUSA o HoT (não consome tick) em vez de
  cancelar — se o player reviver, os ticks restantes continuam válidos.
- `_handle_player_death` — passou a remover `ActiveManaRegen` também
  (só removia `ActiveRegen`/HP antes — assimetria pequena, cinto-e-
  suspensório com o guard acima).

Testes: novo `tests/test_consumables.py` (8 casos, área sem cobertura
nenhuma antes) — poção/HoT rejeitados com player morto (`heal_instant`,
registro de `ActiveRegen`, `consumable_ok` nunca enviado ao morto);
HoT pausa (não cura, não consome tick) quando o player morre no mesmo
tick em que o HoT tocaria; regressão de HoT funcionando normal pra
player vivo; `ActiveManaRegen` removido na morte. Prova diferencial
confirmada nos 3 gates (desligado → teste correspondente falha, religado
→ passa). Suíte completa: 906 passed (3x limpa).

**Verificação pendente**: pedir pro usuário confirmar em teste manual —
morrer com poção HoT ativa não deve mais causar "corpo reagroa mob";
spammar poção até morrer não deve mais consumir o item na morte.

#### §34.74.47 — Fase 7: hotbar server-autoritativa na persistência (2º bug de playtest — recorrente) (06/08/2026)

Segundo dos 3 bugs de playtest. Este era o que o usuário suspeitava ser
arquitetural — já tinha pedido esse fix antes, funcionou nos primeiros
testes, "desconfigurou de novo" numa sessão posterior. Investigado por
agente de background e RE-VERIFICADO linha a linha nesta sessão de
planejamento (não só o relatório do agente) antes de qualquer código.

**Fonte de verdade viva já estava correta**: `_handle_hotbar_update`
(`server/session.py:1152`, fix de 05/08 já documentado) já aplica
reordenação DIRETO no `PlayerSkills` ao vivo do ECS — durante a sessão,
a hotbar em uso nunca quebra. O bug era só na PERSISTÊNCIA (o que é
gravado no banco e lido no próximo login).

**Mecanismo confirmado**:
1. `client/save_sync_handlers.py::_collect_save_state` manda `skills:
   {"learned": [...]}` DE PROPÓSITO sem "hotbar" (comentário: "layout é
   UI local"). Falso pro servidor — `world_server.py:1629` no login
   precisa de `hotbar` salvo, senão cai no fallback de reconstruir
   slots a partir de `learned_skill_ids` (`set`, ordem não garantida).
2. `_handle_hotbar_update` faz merge parcial correto no CACHE da sessão
   (`session.last_client_payload["skills"]["hotbar"]`) — mas não
   persiste sozinho.
3. `_handle_save_state` (`session.py:699`) faz **replace total**:
   `session.last_client_payload = payload`. Como o payload do cliente
   nunca tem "hotbar" (item 1), esse replace apaga o que o item 2
   tinha posto — e já persiste na sequência.
4. `client/network_handlers.py:1778` — QUALQUER STATS_UPDATE com
   `inv_snapshot`/`equip_snapshot` dispara um SAVE_STATE "mudo" (sem o
   jogador perceber). `enter_normalized_progression`/
   `exit_normalized_progression` (`instance_progression.py`) SEMPRE
   passam `inv=`/`equip=` — ou seja, ENTRAR OU SAIR da BG sempre
   dispara esse SAVE_STATE mudo.
5. `_build_save_merge` usava `client_p["skills"]` INTEIRO, sem merge
   por subcampo — "hotbar" ausente lá = ausente no que persiste.

**Por que "funcionou, depois desconfigurou de novo"**: a hotbar ao vivo
nunca quebra (item 2); só o valor GRAVADO NO BANCO fica errado, e isso
só aparece no PRÓXIMO LOGIN. Numa sessão contínua tudo parece certo o
tempo todo — só num login seguinte (depois de qualquer SAVE_STATE mudo
ter rodado no meio do caminho — BG é o gatilho mais confiável, mas
qualquer autosave periódico/troca de mapa também dispara) o
`skills_json` corrompido no banco aparece como hotbar embaralhada.

**Precedente exato já no código**: `_build_save_merge` já documentava
o MESMO padrão de bug, já corrigido, pro EQUIPAMENTO ("aljava sempre
voltava cheia no relogin porque client_payload[...] é só um cache...
nunca atualizado quando flechas são gastas... `live_equipment` —
Equipment ATUAL do ECS — elimina a janela de staleness"). Esta Fase 7
aplica o MESMO padrão pra hotbar, em vez de reinventar.

**Fix**: novo `WorldServer.get_player_hotbar_data(session_id) -> list`
(mesmo lugar/padrão de `get_player_equipment_data`) — lê
`PlayerSkills.skills` ao vivo, devolve `[sk.skill_id if sk else None
for sk in ps.skills]`. `_persist_character` busca esse valor
(`live_hotbar`) junto de `live_equipment` e repassa pra
`_build_save_merge`, que agora sobrescreve `skills["hotbar"]` com ele
quando presente — incondicional, mesmo espírito de `live_equipment`.
`learned_skill_ids` continua vindo do cliente (fallback servidor) sem
mudança — só o subcampo "hotbar" passou a vir sempre do ECS vivo.

Testes: `tests/test_hotbar_bg_leak.py` ganhou uma 4ª seção (2 casos,
reaproveitando `make_session_manager`/`fake_login` de
`tests/test_session.py` e o padrão de mensagem real via
`mgr.on_message`, não chamada interna direta) — reordena hotbar via
HOTBAR_UPDATE, manda um SAVE_STATE "mudo" (payload idêntico ao que
`_collect_save_state` monta hoje, sem "hotbar"), confirma que
`_persist_character` ainda devolve a hotbar certa (antes do fix,
`KeyError: 'hotbar'` — reproduziu o bug real exatamente); regressão de
`learned` continuando a vir do payload do cliente. Prova diferencial
confirmada (fix desligado → `KeyError`, religado → passa). Suíte
completa: 908 passed (3x limpa).

**Verificação pendente**: pedir pro usuário confirmar em teste manual
REAL — reordenar hotbar, entrar/sair da BG algumas vezes, RELOGAR (não
só continuar conectado) e confirmar que a hotbar salva bate com a que
estava antes de entrar na BG.

#### §34.74.48 — Fase 8: minion como candidato válido em `_resolve_target` (3º bug de playtest) (06/08/2026)

Terceiro e último bug do cluster de playtest. A hipótese original do
agente de background (Enemy/AIControlled ausente no CLIENTE) foi
re-verificada linha a linha e DESCARTADA — mas a investigação continuou
(a pedido do usuário) até achar a causa real, do lado ERRADO que o
agente tinha apontado: não é o cliente, é o SERVIDOR.

**Hipótese descartada**: minion não tem `Enemy`/`AIControlled` no
cliente, então `_resolve_target` nunca acha minion. Falso — o cliente
recebe TODO minion com `kind: "enemy"` (`world_server.py:2318`) e
reconstrói via `_spawn_remote_mob` → `create_enemy()`
(`client/remote_entity_handlers.py:808-826`), a MESMA fábrica de mob
comum, que sempre anexa `Enemy`+`AIControlled`. A cópia local do minion
no cliente tem os mesmos componentes que qualquer mob.

**Causa real**: `ui/skill_handlers.py`/`ui/systems.py::SkillSystem` é
código COMPARTILHADO — a mesma classe roda no cliente (predição) E no
servidor, autoritativo (`server/world_server.py:479`: `self.
_skill_system = SkillSystem(...)`, usado por
`server/skill_processor.py:314` via `getattr(self._skill_system,
f"_skill_{sid}")`). `_resolve_target` (`ui/systems.py:4810`) tem 2
loops de fallback (usados quando não há alvo explícito válido — alvo
anterior morreu, "spammar" a tecla sem re-clicar), ambos exigindo
`Enemy`. `create_minion` (`engine/entity_factory.py:740-828`) nunca
anexa `Enemy` no SERVIDOR — decisão deliberada (mesmo espírito de
Torre, `MinionSystem` próprio, não `EnemyAISystem`). Quando o alvo
explícito cai (minion morre — comum, TTK baixo) e o código tenta
auto-selecionar o hostil mais próximo, minion nunca é candidato — skill
falha silenciosamente do lado SERVIDOR (que é quem importa), mesmo com
o cliente parecendo ok. Desincronização real, exatamente como o usuário
suspeitou.

**Desvio investigado e descartado**: suspeitei que `Visible` (também
exigido pelos 2 loops) nunca estaria presente no servidor (`FogSystem`,
que normalmente mantém essa tag, só existe em `ui/systems.py`,
client-side, nunca instanciado no servidor) — o que quebraria a
auto-seleção pra QUALQUER mob, não só minion. Achei o mecanismo que já
resolve isso: `world_server.py:4627-4646`, o loop de registro de mob
novo (`Combatant` genérico, roda todo tick) já anexa `Visible`
manualmente a qualquer `Combatant` novo, comentário já existente:
"Visible: EnemyAISystem filtra por Visible (FogSystem não roda no
servidor)" — esse gap já tinha sido encontrado e resolvido antes, de
forma genérica. `Visible` não era o problema — só `Enemy`.

**"Alvo inválido" (mensagem separada)**: continua sendo o que o agente
já tinha identificado — race de latência genérica (alvo morreu entre o
clique e o processamento), sem relação com o achado acima. Mais
frequente com minion por morrer rápido, não é bug de código — fora de
escopo.

**Knockback perto de minion / Interceptar**: mecanismo do agente não
desmentido (predição otimista do cliente vs. minion sendo o primeiro
mob com movimento contínuo de verdade) — mas a mitigação sugerida
(minion ausente do obstáculo de rota do cliente, `_get_enemy_tiles`)
também não se sustentou: essa função filtra por `Enemy` no CLIENTE, e
minion TEM `Enemy` lá. Sem fix de baixo risco óbvio — o "snap" de
correção já é deliberado (`server/session.py:434-438`), suavizar isso é
reconciliação client-side bem maior (qualidade de animação, mais
arriscado). Fica de fora, registrado sem fix — mesmo espírito do
descarte de `TICK_RATE` dinâmico na Fase 5.

**Fix**: novo loop paralelo em `_resolve_target` — `get_entities_with(
Position, Minion, TileMovement, CombatStats, Visible)`, mesma lógica de
melhor-candidato (menor HP, desempate por distância) do loop de `Enemy`
já existente, resultado combinado no mesmo `best_id`. Não muda o loop
de `Enemy` — só adiciona a fonte de candidato que faltava.

Testes: `tests/test_resolve_target_hostility.py` ganhou uma classe nova
(3 casos) — minion hostil sozinho (sem mob comum por perto) é
auto-selecionado (hoje retornaria -1, reproduz o bug real); minion
neutro não é selecionado (mesma regra de hostilidade do mob comum);
mob comum e minion hostis não se atrapalham (regressão do loop
existente). Prova diferencial confirmada (loop desligado → volta a dar
-1, religado → passa). Suíte completa: 911 passed (3x limpa).

**Verificação pendente**: pedir pro usuário confirmar em teste manual —
matar um minion com o alvo selecionado nele e continuar spammando PNQ
sem re-clicar deveria auto-mirar o próximo minion hostil adjacente, em
vez de "não fazer nada". Fecha os 3 bugs do cluster de playtest desta
sessão (§34.74.46/47/48).

#### §34.74.49 — Fase 9: talentos server-autoritativos na persistência — 3ª ocorrência do padrão "cache de client_payload nunca invalidado" (06/08/2026)

Bug novo, reportado depois de fechar o cluster anterior: personagem
"Nalthor" (guerreiro, level 6, deveria ter só 5 pontos alocados) saiu
da BG com TODOS os talentos no máximo — o preset que só deveria existir
DENTRO da instância. Investigação 100% verificada linha a linha nesta
sessão, sem precisar de agente de background (contexto já fresco da
Fase 7, mesmos arquivos).

**Nomeando o padrão** (3ª vez nesta sessão — equipamento, antes desta
sessão; hotbar, §34.74.47; talentos, agora): campo persistido via
`session.last_client_payload` (cache do último payload recebido do
cliente), sem uma fonte viva do servidor como fallback/autoridade,
nunca invalidado quando o servidor muda o estado real por baixo do
cache (troca de componente pra um overlay temporário de instância, ou
qualquer outra mutação server-side que o cliente não reflete de volta
imediatamente).

**Mecanismo confirmado**:
1. `enter_normalized_progression` (`server/instance_progression.py:
   208-212`) cria o overlay: `new_tt.allocated = {tid: t["max_points"]
   for tid, t in TALENTS.items() if t.get("build") ==
   new_tt.chosen_build}` — todo talento do build no máximo, decisão de
   design da instância.
2. `_handle_talent_update` (`server/session.py:930-950`) — se o player
   mandar TALENT_UPDATE enquanto dentro (ex.: testando build na
   instância), `validate_talent_allocation`
   (`server/world_server.py:3477-3562`) lê o `TalentTree` AO VIVO —
   dentro da instância, é o overlay maxado — `total_budget` vira um
   número gigante, qualquer claim passa. O resultado (ainda maxado) é
   cacheado em `session.last_client_payload["talents"]` incondicional.
3. Mesmo SEM TALENT_UPDATE nenhum: diferente de hotbar (que
   `_collect_save_state` omite de propósito), o cliente MANDA "talents"
   em todo SAVE_STATE — como a cópia local do cliente também reflete o
   overlay (recebido via STATS_UPDATE ao entrar,
   `instance_progression.py:239`), QUALQUER SAVE_STATE enviado dentro
   da instância (autosave periódico, ou o SAVE_STATE "mudo" que a Fase
   7 já documentou disparar ao entrar/sair de BG) também cacheia o
   overlay maxado — não precisa nem mexer em talento pra vazar.
4. `_persist_character` corretamente NÃO persiste com
   `is_in_normalized_progression=True` — sem dano imediato. Mas nada
   limpa o cache quando `exit_normalized_progression` restaura o
   TalentTree real — o cache fica maxado independente da ECS real.
5. `_build_save_merge`: `"talents": client_p.get("talents")` — usava o
   cache poluído direto, SEM fallback pro servidor (diferente de
   "skills", que já tinha fallback pra `srv_data`, e de "equipment",
   que já usa `live_equipment`). Qualquer persist depois de sair da
   instância grava os talentos maxados no banco.

**Fix**: mesmo padrão de `get_player_equipment_data`/
`get_player_hotbar_data` — novo `WorldServer.get_player_talent_data
(session_id)`, lê `TalentTree` ao vivo (`chosen_build`, `allocated`,
`available_points`). Como `_persist_character` já recusa persistir
dentro da instância, o `TalentTree` vivo no momento em que esse helper
roda é sempre o real. `_build_save_merge` ganha `live_talents`, usa
incondicional quando presente. Não mexe em `_handle_talent_update`/
`validate_talent_allocation` — alocar talento livremente DENTRO da
instância continua permitido (é o propósito dela); só a PERSISTÊNCIA
passa a ignorar o cache poluído.

Testes: `tests/test_hotbar_bg_leak.py` ganhou uma seção nova (2 casos)
— entra em progressão normalizada, manda TALENT_UPDATE com a alocação
maxada do overlay, sai da instância, confirma que `_persist_character`
devolve os talentos REAIS (2 pontos, não os ~30 maxados — antes do fix,
a saída do teste mostrou literalmente TODOS os `cav_*` no máximo,
reproduzindo o "Nalthor" relatado); regressão de alocação normal fora
de instância. Prova diferencial confirmada (fix desligado → dump
completo do overlay maxado no assert, religado → passa). Suíte
completa: 913 passed (3x limpa).

**Verificação pendente**: pedir pro usuário confirmar em teste manual
real — entrar na BG, sair, RELOGAR, conferir que os talentos batem com
o nível real (ex.: level 6 = 5 pontos), não com o preset maxado.

#### §34.74.50 — Fase 10: colisão de slot na hotbar de instância (4º bug de playtest) + diagnóstico do rollback do Interceptar (06/08/2026)

Dois itens vindos de prints reais do usuário testando a BG (personagem
"Nalthor"): hotbar dentro da instância com skills fora do atalho
configurado mesmo estando na hotbar real, e o Guerreiro "levando
rollback" depois de usar Interceptar — anima o dash inteiro e depois
volta/corrige de repente.

**Item A — colisão de slot na hotbar de instância.**
`_grant_instance_skill` (`server/instance_progression.py`) tenta
colocar cada skill desbloqueada no mesmo slot da hotbar REAL do player
(`real_slot_by_sid`), mas só se esse slot ainda estiver livre NO
MOMENTO da concessão — senão cai greedy no primeiro slot vazio.
`INSTANCE_SKILL_UNLOCK_ORDER` (`content/skill_config.py`) é uma ordem
FIXA por classe, independente da hotbar real do player. Uma skill que
desbloqueia CEDO nessa ordem mas não está na hotbar real do player cai
no primeiro slot vazio — que pode ser exatamente o slot que uma skill
desbloqueada DEPOIS legitimamente quer. Quando essa segunda skill
finalmente desbloqueia, o slot dela já foi tomado pela "de
preenchimento" e ela cai em outro lugar.

**Fix**: `_grant_instance_skill` ganhou parâmetro `reserved_slots:
set[int] | None` — pré-computado uma vez (`enter_normalized_progression`
e `_process_instance_levelup`) como `set(real_slot_by_sid.values())`.
Skills sem slot preferido (ou cujo preferido já foi ocupado) nunca
escolhem um slot reservado, mesmo que a skill dona ainda não tenha
desbloqueado — só usam um reservado como último recurso, se não
sobrar nenhum slot livre não-reservado.

Testes: `tests/test_hotbar_bg_leak.py::TestInstanceSkillPreferredSlot`
ganhou `test_skill_de_preenchimento_nao_rouba_slot_reservado_por_skill_tardia`
— hotbar real com "interceptar" (não "golpe_poderoso") no slot 0,
entra na instância, confirma que o slot 0 não vira "golpe_poderoso";
avança level via `_process_instance_levelup`, confirma que o slot 0
finalmente vira "interceptar". Prova diferencial confirmada
(`reserved_slots` zerado → teste falha exatamente como o bug relatado,
`'golpe_poderoso' == 'golpe_poderoso'` → religado, passa). Suíte
completa 3x limpa.

**Item B — diagnóstico do rollback do Interceptar (sem fix ainda).**
Antes de mexer em qualquer coisa: investigação confirmou que a
predição visual local do dash do Interceptar já tinha sido REMOVIDA em
22/07/2026 (comentário em `ui/systems.py:4769-4780`), exatamente por
este mesmo padrão de queixa antes — `_skill_interceptar` só é chamado
pelo branch OFFLINE de `_use_skill`, nunca por `_use_skill_visual_only`
(caminho online). Ou seja, o dash observado hoje NÃO é predição local
sendo desfeita — é uma animação que só começa quando o cliente recebe
uma correção do SERVIDOR já confirmando sucesso (`ENTITY_MOVE` com
`is_dash=True`), e mesmo assim está sendo revertida depois. Mecanismo
real desconhecido; hipótese de trabalho (não confirmada): uma segunda
`ENTITY_MOVE` conflitante — de um MOVE normal (ex.: perseguição já em
andamento) processado perto do CAST_SKILL — chega DEPOIS da confirmação
do dash e cai no branch `DESYNC_CORR` de
`client/network_handlers.py::_handle_msg_entity_move`, que cancela
qualquer dash em andamento (`_was_dashing=True`) e snapa a posição.
Já existe um comentário anterior em `server/skill_processor.py`
alertando pra corrida MOVE-vs-CAST_SKILL nesse mesmo ponto — não é
uma hipótese nova, é um risco já documentado no código.

Como não dá pra confirmar só lendo código, item B ficou só
instrumentação de diagnóstico (mesmo padrão de `debug/aoi_debug.py`:
env var `RPG_DEBUG_INTERCEPTAR`, OFF por padrão, log em arquivo local
por processo — servidor e cliente cada um grava o seu):
- Novo `debug/interceptar_debug.py` — singleton `INTERCEPTAR_DBG.log(event, **kwargs)`,
  grava em `debug/logs/interceptar_debug.log`.
- Servidor (`server/skill_processor.py`): `DASH_OK` no ponto de sucesso
  do Interceptar (`_skill_position_corrections.append` com
  `is_dash=True`), `DASH_REJECTED` no ponto de falha.
- Servidor (`server/session.py::_handle_move`): `MOVE_REQUEST` em TODA
  tentativa de MOVE normal do player (tick, tx/ty pedido,
  aceito/rejeitado) — pra ver se um MOVE comum é processado perto do
  CAST_SKILL do Interceptar.
- Cliente (`client/network_handlers.py::_handle_msg_entity_move`, bloco
  `eid == self._my_eid`): `DASH_KEEP` (predição já bate, mantém),
  `DASH_FORCED` (correção com `duration` explícito, ex. knockback),
  `DASH_START` (inicia ou enfileira a animação do dash — fluxo normal
  do Interceptar), `DESYNC_CORR` (branch de correção genuína — é AQUI
  que o cancelamento do dash em andamento acontece, se acontecer).

Mudança é logging puro, sem lógica nova — suíte completa rodada 1x
(sem necessidade de prova diferencial).

**Verificação pendente**: pedir pro usuário setar
`RPG_DEBUG_INTERCEPTAR=1` nos dois lados (servidor e cliente, são
processos separados) antes do próximo teste, reproduzir o rollback, e
mandar os dois `debug/logs/interceptar_debug.log` — com a sequência
real de eventos dá pra confirmar (ou descartar) a hipótese da corrida
MOVE-vs-CAST_SKILL e desenhar o fix de verdade numa próxima rodada, em
vez de adivinhar.

#### §34.74.51 — Fase 0 do roteiro de saneamento: fecha os 2 bugs A4 confirmados pela auditoria (07/08/2026)

Primeira fase do roteiro arquitetural aprovado em
`C:\Users\l4nce\.claude\plans\expressive-wondering-starlight.md`
(`arquitetura/VISAO_PRODUTO.md` define o rumo: sistemas redondos antes
de conteúdo). Fecha os 2 bugs reais achados na auditoria de 06/08/2026
(`PROBLEMAS_ARQUITETURA.md` §12) — quarta e quinta ocorrência do
mesmo padrão A4 (equipment/hotbar/talents já corrigidos antes na
mesma sessão).

**`inventory`** (`server/session.py::_build_save_merge`, linha ~301):
nunca teve fallback ao vivo, só `client_p.get("inventory")`. Fix: novo
`WorldServer.get_player_inventory_data(session_id)` (`world_server.py`,
lê `Inventory` ao vivo, mesmo padrão de `get_player_equipment_data`),
usado via `live_inventory if live_inventory is not None else
client_p.get("inventory")` — `is not None`, não truthy, porque uma bag
genuinamente vazia (`[]`) é estado real válido, não "indisponível".

**`skills["learned"]`** (mesmo método, linha ~258-260): só o subcampo
`hotbar` tinha sido corrigido na Fase 7 (§34.74.47) — `learned`
continuava preferindo o cache do cliente quando truthy. Vetor real:
desconectar DENTRO da instância (`on_disconnect` restaura o
`PlayerSkills` real via `exit_normalized_progression` e chama
`_persist_character` na sequência, sem passar por
`_handle_save_state`/`sync_player_skills` — o cache nunca é corrigido
antes do save). Fix: inverte a prioridade — `srv_data.get("skills")`
(sempre ao vivo, `get_player_save_data` lê `ps.learned_skill_ids` na
hora) vence por padrão; fora da instância isso não perde nada porque
`sync_player_skills` já aplicou o que o cliente reportou ANTES de
`_persist_character` rodar, no mesmo handler de SAVE_STATE.

**Testes**: `tests/test_hotbar_bg_leak.py` ganhou
`TestInventoryPersistedFromLiveECS` (3 testes: sobrevive a desconexão
dentro da instância, bag vazia de verdade não é tratada como
indisponível, fluxo normal via INV_SYNC continua funcionando) e
`test_learned_vem_do_servidor_ao_vivo_nunca_do_cache` em
`TestHotbarPersistedFromLiveECS` (reproduz o vetor de desconexão).
Prova diferencial: os dois fixes desligados temporariamente (marcador
`# DIFFERENTIAL-PROOF-TEMP`), confirmado que exatamente os testes que
deveriam discriminar falham (e só esses — os testes de fluxo normal
continuam passando mesmo com o fix desligado, como esperado), religado.
Suíte completa: 918 passed, 2x limpa consecutiva (3ª rodada em
andamento em background no momento deste registro).

**Achado lateral durante os testes**: `TestInventoryPersistedFromLiveECS`
inicialmente assumia "personagem recém-logado = inventário vazio" —
falso, porque a suíte reaproveita o MESMO username entre métodos de
teste (mesmo padrão das outras classes deste arquivo) e o banco de
teste persiste entre execuções; um método anterior que persiste um
item de verdade contamina o "estado fresco" assumido pelo próximo.
Fix nos próprios testes: `inv.items.clear()` explícito antes de cada
cenário, em vez de assumir estado — mesma disciplina que as outras
classes do arquivo já seguem (nunca assumir "vazio por padrão", sempre
montar o estado exato que o teste precisa).

#### §34.74.52 — Fase 2 do roteiro de saneamento: mata dual-mode `if self._net` (07/08/2026)

Escopo real ficou menor que a estimativa do roteiro (13 pontos reais em
3 arquivos, não 47 em 5 — detalhe completo da classificação em
`PROBLEMAS_ARQUITETURA.md` §13, seção "✅ Fase 2"). Fechados 11 pontos +
1 achado bônus (`_use_skill` inteiro, corpo morto desde sempre porque
`_server_authoritative` nunca varia neste branch, virou wrapper de 1
linha; a flag em si removida de `ui/systems.py`/`game.py`/
`server/world_server.py`). Deferido pra Fase 3: os 2 pontos dentro de
`_use_skill_visual_only` (função de ~400 linhas, entrelaçada demais pra
ser "matar dual-mode" mecânico — é o próprio alvo do redesenho B2/B3).

**Regressão achada no playtest manual** (vazou da Fase 1, não desta
fase): `ui/quest_system.py::_fallback_marker_cache` tinha sido movido
pro `__init__` da classe ERRADA (`QuestSystem` em vez de
`QuestDialogSystem`) — `AttributeError` ao clicar em NPC de quest sem
ícone customizado. Suíte automatizada não cobre esse caminho de render;
só apareceu no playtest. Corrigido, reproduzido fora da suíte,
confirmado pelo usuário em jogo.

**Suíte**: 930/930, 3 rodadas limpas ao longo da fase + playtest manual
confirmando loja/consumíveis/Pirofagia/transição de mapa/F12 debug.

#### §34.74.53 — Fase 3, piloto: `build_channeling_from_skill` (Calamidade Flamejante, 07/08/2026)

Pesquisa (Veloren `CharacterState`, l33l33.com/DeepWiki) + levantamento
das 26 skills do catálogo antes de desenhar; plano formal aprovado via
EnterPlanMode/ExitPlanMode antes de codar. Detalhe completo (desenho,
achado corrigido 2x sobre `ChannelingSystem._apply_tick` — não era brecha
de segurança nem lógica offline viva, era código morto de verdade — e
lista de arquivos) em `PROBLEMAS_ARQUITETURA.md` §13, seção "✅ Fase 3 —
piloto". Resumo: `content/skill_config.py` ganha `params` pra Calamidade
Flamejante; `engine/core_systems.py::build_channeling_from_skill` vira
fonte única catálogo → `Channeling`; `AoeTargetingSystem._start_channel`
generaliza de string hardcoded pra `skill.is_channeled`;
`ChannelingSystem._apply_tick` (laço de dano morto no cliente) removido.

**Suíte**: `tests/test_calamidade_channel_config.py` (novo, 3 testes,
prova diferencial feita) + `tests/test_flt_dedup.py` (existente, não
quebrou) + suíte completa 933/933.

#### §34.74.54 — Sistema de GM server-autoritativo (destrava F12 pra playtest real, 07/08/2026)

Usuário tentou validar o piloto do Fase 3 (§34.74.53) via F12 (subir
nível/talento) e a skill foi recusada pelo servidor — causa raiz: F12
só mutava ECS local do CLIENTE, servidor nunca sabia de nada, então
qualquer autorização real falhava. Pesquisa (AzerothCore: coluna
`gmlevel` na conta) + plano aprovado via EnterPlanMode antes de codar.

- `accounts.is_gm` (SQLite, `server/auth.py`) — concedida só via
  `python -m server.grant_gm <username>` (CLI direto no banco, sem UI
  nem endpoint de rede pra conceder, mesmo espírito do `account set
  gmlevel` do AzerothCore).
- 3 mensagens novas (`shared/messages.py`): `GM_LEVELUP {levels}`,
  `GM_ADD_GOLD {amount}`, `GM_ADD_ITEM {item_name}` — mesmo molde de
  `BUY_REQUEST`. Sem `is_gm` na conta, servidor ignora silenciosamente
  (mesmo padrão de bypass negado de `skill_processor.py` pra taunt/
  stun) — sem confirmar nem negar, zero informação pra quem tentar sem
  autorização.
- Handlers (`server/session.py`) reaproveitam os MESMOS caminhos
  autoritativos já usados por recompensa de quest/loja — `process_levelups`
  (`engine/stats_system.py`, já concede talent points),
  `queue_stats_update` (nível/gold), `INVENTORY_UPDATE` (item, resolvido
  pelo mesmo catálogo `content.loot_tables._T`+`SHOPS` que o F12 já
  listava). Nenhum caminho de mutação novo — só mais 3 pontos de entrada
  pro que já existia.
- `LOGIN_OK` ganha `is_gm: bool` (`Session.is_gm`, setado em
  `_handle_login` a partir de `authenticate()`); F12 só abre/renderiza
  com `DEBUG_MODE and self.is_gm` (dupla checagem — config local E conta
  GM). Aba Mapa do F12 não mudou — já usa `ZONE_CHANGE_REQ`, validado
  desde o Fase 2 (§34.74.52).

**Suíte**: `tests/test_gm_commands.py` (novo, 7 testes — nega sem GM,
aplica com GM, item desconhecido não quebra) + prova diferencial no
gate de `is_gm` (removido temporariamente, confirmado que o teste sem-GM
falhava, revertido) + suíte completa 940/940.

#### §34.74.55 — `config.py::save()` crashava o jogo com 2 clientes na mesma pasta (07/08/2026)

Crash real relatado pelo usuário durante playtest (alocando talento, via
`request_autosave()` → `_save_config()`): `PermissionError: [WinError 5]
Acesso negado` em `os.replace(tmp_path, CONFIG_FILE)`. O write atômico
de `config.json` (tmp + replace, já existia desde 20/07/2026 pra outro
bug — leitura vazia com 2 clientes na mesma pasta) usava um nome de
arquivo temporário FIXO (`config.json.tmp`) — 2 processos salvando quase
ao mesmo tempo colidiam nesse mesmo `.tmp`, e o `os.replace()` de um
podia achar o arquivo já aberto pelo outro. Sem try/except nenhum, isso
propagava e derrubava o cliente inteiro — pior que o bug original (que
só dava leitura vazia tolerada).

Fix: `tmp_path` único por processo (`config.json.<pid>.tmp`) — elimina a
colisão entre 2 clientes — + `try/except OSError` ao redor do write/
replace, pra qualquer falha residual (antivírus/backup segurando o
arquivo) degradar como "autosave pulou esta vez" (log, sem exceção) em
vez de crashar. `tests/test_config.py` ganhou 2 testes (nomes de tmp não
colidem entre PIDs diferentes; `os.replace` mockado pra falhar não
propaga) — prova diferencial feita nos dois.

**Suíte**: 942/942, limpa.

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

**Conceder GM** (menu de debug F12 — nível/ouro/itens de verdade, ver
§34.74.54): `python -m server.grant_gm <username>` (com o servidor
rodando ou parado, mexe direto no banco). Precisa também
`"debug_mode": true` em `config.json` (F12 exige as duas: conta GM E
config local). Revogar: `python -m server.grant_gm <username> --revoke`.
