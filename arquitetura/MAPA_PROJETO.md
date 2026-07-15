# Mapa do Projeto — RPG ECS Online

> Guia rápido para localizar qualquer parte do projeto.
> Branch: **online** — versão multiplayer em desenvolvimento paralelo ao `master`.
> Última atualização: 2026-06-27

> **ATENÇÃO:** Ler `arquitetura/ARQUITETURA_ONLINE.md` antes de qualquer trabalho neste branch.

---

## Onde encontrar o quê — Online (ONLINE-ONLY)

| Quero… | Arquivo | Seção |
|--------|---------|-------|
| Empacotar o cliente pra distribuição (alpha) | `release_tools/build_client.ps1` + `release_tools/rpg_online_client.spec` | PyInstaller onedir; assets/maps ao lado do exe; LEIA-ME gerado; config.json criado no 1º run ao lado do exe (`config.py`) |
| Definir/modificar tipo de mensagem | `shared/messages.py` | `MsgType` enum + docstring do payload |
| Adicionar handler de mensagem no servidor | `server/session.py` | `_handlers` dict + `async def _handle_*` |
| Alterar constante de rede (tick rate, AOI, etc.) | `shared/constants.py` | constante direta |
| Lógica de autenticação / persistência | `server/auth.py` | `authenticate()`, `save_character()` |
| Loop de ticks / ECS headless | `server/world_server.py` | `WorldServer._tick()` |
| Spawn/despawn de player | `server/world_server.py` | `spawn_player()`, `despawn_player()` |
| Transição de mapa (servidor) | `server/world_server.py` | `transfer_player()`, `get_player_map()`, `_map_bundles`, `_eid_to_map`, `_player_maps` |
| Transição de mapa (cliente) | `client/network_handlers.py` | `_handle_msg_zone_change()` → `_do_transition()` |
| Handler ZONE_CHANGE_REQ | `server/session.py` | `_handle_zone_change_req()` — valida tile + mapa, chama `transfer_player` |
| Save merge (autoridade por campo) | `server/session.py` | `_build_save_merge()` |
| Processar mortes de mobs no servidor | `server/server_death_handler.py` | `ServerDeathHandler.update()` |
| Gerenciar sessões e broadcast AOI | `server/session.py` | `SessionManager` |
| AOI subscription (known_eids) | `server/session.py` | `_build_update_for_session()` |
| Derivar stats de equipamento/talentos (server-autoritativo) | `server/world_server.py` | `_apply_equipment_modifiers()`, `_apply_talent_modifiers()` |
| Re-aplicar talentos ao ECS do servidor | `server/world_server.py` | `apply_talent_effects_to_player()` |
| Skill Level (xp/bônus, Tibia-like) | `engine/stats_system.py` | `grant_skill_xp()`, `apply_skill_bonuses_to_combat()`, `weapon_skill_extras()`, `defense_skill_extras()` |
| Hooks de xp de Skill Level no servidor | `server/spell_completion_processor.py`, `ui/systems.py`, `engine/core_systems.py` | `_server_apply_ranged_physical()`, `_server_apply_magic_damage()`, `CombatSystem.deal_damage(is_server=)`, `StatusEffectSystem._apply_tick()`/`_on_resisted_dot()` |
| Progresso/entrega de quest (server-autoritativo) | `server/world_server.py` | `_process_quest_events()`, `move_player()`/`apply_consumable()`/`update_player_equipment()` (gatilhos) |
| Aceitar/entregar quest (QUEST_ACCEPT/QUEST_TURN_IN) | `server/session.py` | `_handle_quest_accept()`, `_handle_quest_turn_in()` |
| Lógica pura de quest (matching, progresso, recompensa) | `engine/quest_logic.py` | `apply_event()`, `try_start()`, `complete_quest()`, `can_turn_in()` |
| Trade (player↔player) — servidor | `server/trade_processor.py` | `TradeProcessorMixin`, `TradeSession` — request/aceite/oferta/gold/confirma/cancela |
| Trade (player↔player) — handlers de rede | `server/session.py` | `_handle_trade_*` (8), hook de desconexão em `on_disconnect` |
| Trade (player↔player) — cliente | `client/trade_handlers.py` | `TradeHandlers` — popup Shift+clique, convite, janela (5 slots+gold) |
| Trade (player↔player) — estado de UI | `ui/ui_components.py` | `TradeUIState` (componente ECS no player) |
| Chat (texto, 3 abas Local/Mundial/Combate) — cliente | `client/chat_handlers.py` | `ChatHandlers` — Enter abre campo, digita, Enter envia; abas, scrollbar, wrap de linha (500 entradas/aba) |
| Chat — balão de fala acima da cabeça | `ui/chat_bubble.py` | `ChatBubbleManager`/`CHAT_BUBBLE` — rastreia Position ao vivo (diferente de `ui/floating_text.py`) |
| Chat — aba "Combate" (log de dano/cura/proc/loot) | `ui/combat_log.py` | `CombatLog`/`LOG` — histórico persistente (500), `add(text, color)` mesma assinatura de sempre, `entries` property lida pela aba |
| Iniciar o servidor | `server/main.py` | `py -3.10 server/main.py` |
| Conectar cliente ao servidor | `client/network.py` | `NetworkClient` |
| Banco de dados / schema | `data/game.db` (SQLite) | criado por `auth.init_db()` |

## Onde encontrar o quê — Compartilhado (COMPARTILHADO)

| Quero… | Arquivo | Seção |
|--------|---------|-------|
| Definir constantes rede/mundo | `shared/constants.py` | direto |
| Encodar/decodar mensagem | `shared/messages.py` | `encode()`, `decode()` |

## Onde encontrar o quê — Offline/herdado (COMPARTILHADO com cliente online)

| Quero… | Arquivo | Seção |
|--------|---------|-------|
| Criar/modificar uma skill | `content/skill_config.py` | `SKILL_CATALOG` |
| Implementar handler de skill do guerreiro | `ui/skill_handlers.py` | `_skill_<id>` |
| Range check de skill (pixel-based) | `ui/skill_handlers.py` | `MELEE_RANGE_PX`, `_range_ok()`, `_melee_ok()` |
| Implementar skill do mago | `ui/skill_handlers.py` + `ui/spell_system.py` | `_skill_*` + `_complete_cast` |
| Fórmula de dano + is_ability miss bypass | `engine/damage_calculator.py` | `resolve_attack_outcome(is_ability=)` |
| Funções de stat (modifier, combat) | `engine/stat_fns.py` | `add_modifier`, `enter_combat`, etc. |
| Stats base por classe / attack interval | `engine/stats_system.py` | `CLASS_BASE_STATS`, `sync_attack_interval()` |
| Adicionar talento | `content/talent_data.py` | `TALENTS` + `CLASS_BUILD_MAP` |
| Efeito de talento no jogo | `ui/talent_system.py` | `apply_talent_effects()` |
| Painel read-only de Skill Level (tecla L) | `ui/skill_level_ui.py` | `SkillLevelUI`, registrado em `client/modal_stack_handlers.py` |
| Criar item/arma/arco/aljava (catálogo único — loot + loja) | `content/item_table.py` | `ITEMS` dict (07/07/2026 — antes duplicado em `content/loot_tables.py`/`content/merchant_data.py`; `loot_tables._T` e `merchant_data`'s stock agora só referenciam daqui) |
| Adicionar drop de mob | `content/mob_definitions.py` | `MOB_TABLE[nome]["loot"]` (dict item_key→chance, chave de `item_table.ITEMS`; `content/loot_tables.py::roll_mob_loot` só lê) |
| Configurar gold de um mob (chance/faixa min-max, 07/07/2026) | `content/mob_definitions.py` | `MOB_TABLE[nome]["gold_chance"/"gold_min"/"gold_max"]` — opcionais, sem eles cai no genérico por tier (`COIN_DROPS`); `content/loot_tables.py::roll_mob_coins` só lê |
| Adicionar item à loja | `content/merchant_data.py` | `SHOPS[shop_id]["stock"]` — `{"factory": item_table.ITEMS["key"], "price": N}` |
| Criar mob novo | `content/mob_definitions.py` | `MOB_TABLE` (raça/classe/cor + `attributes`/`abilities`/`loot`/`xp_given_by_lvl`) |
| Atributos de combate de um mob (HP/dano/velocidade/acerto/crit) | `content/mob_definitions.py` | `MOB_TABLE[nome]["attributes"]` |
| Habilidade especial de um mob (poison/bleed/stun) | `content/enemy_abilities_data.py` + `content/mob_definitions.py` | `ABILITY_DEFS` (dado) + `MOB_TABLE[nome]["abilities"]` (lista) |
| XP concedido por level do mob | `content/mob_definitions.py` | `MOB_TABLE[nome]["xp_given_by_lvl"]` |
| Sons de mob (aggro, death, attack) | `content/mob_definitions.py` | `"sounds"` dict por mob |
| Sons posicionais online | `ui/sound_manager.py` | `play_mob_sounds_at()`, `volume_at()` |
| Componente ECS | `engine/components.py` | categoria relevante |
| Sistema ECS de GAMEPLAY (headless, cliente+servidor) | `engine/world_systems.py` | herdar de `System`; NUNCA importar pygame no topo |
| Sistema ECS de UI/render/input (cliente-only) | `ui/systems.py` | herdar de `System` (re-exportado de `world_systems`) |
| Efeito visual/som em sistema compartilhado | `engine/fx.py` | `from fx import FLT, SOUNDS, ...` (no-op no servidor; cliente vincula via `bind_client_fx()`) |
| Teleporte/knockback/respawn (escrever current_tile) | `engine/utils.py` | `snap_to_tile()` — NUNCA escrever current_tile_x/y direto |
| Escrita final de dano em HP | `engine/core_systems.py` | `apply_damage_core()` — único lugar; mitigação/imunidade nova entra aqui |
| Atributo de combate novo (modifier) | `engine/stat_fns.py` | par `base_X`/`X` em `CombatStats` + 1 entrada em `_MODIFIABLE_ATTRS` (+ `_STAT_CLAMPS`) |
| STATS_UPDATE privado novo (servidor→dono) | `server/world_server.py` | `queue_stats_update()` (schema na docstring) |
| Registrar sistema no loop offline | `game.py` | `_init_systems()` → `self.systems` |
| Adicionar facção / relação hostil-neutro-amigavel | `content/faction_data.py` | `RELATIONSHIP` (par de facção → tier) + `get_relationship()` |
| Checar se entidade pode brigar com outra (facção) | `engine/faction_system.py` | `can_engage()`, `is_hostile()`, `get_relationship_between()` — nunca reimplementar inline |
| Criar mob de combate hostil ("clássico") | `engine/entity_factory.py` | `create_enemy()` (tag `Enemy`) |
| Criar NPC de combate (guarda, etc — facção tipicamente amigável) | `engine/entity_factory.py` | `create_combat_npc()` (tag `NPC`) — ambos compartilham `_build_combat_entity()` |
| Adicionar NPC de combate a um mapa (conteúdo real) | `maps/{mapa}_entities.json` | chave `"combat_npcs"` (lista de `{x,y,faction,name,profession,...}`) — lido por `engine/map_loader.py` + `server/world_server.py::_create_combat_npcs()` |

---

## Estrutura de arquivos

> **Reorganização de 09/07/2026** — a raiz tinha ~60 arquivos `.py` soltos
> misturando dados de conteúdo, lógica ECS headless, UI/render client-only e
> scripts de dev, sem nenhuma pasta. Critério usado pra separar (verificado
> pelo grafo de imports real, não só "impora pygame?"): **quem carrega esse
> módulo em produção** — `content/`/`engine/` = usado pelo servidor headless
> (direto ou transitivo); `ui/` = client-only mesmo quando o arquivo em si
> não importa pygame (ex.: `combat_log.py`, `skill_handlers.py`, `fov.py` —
> só `ui/systems.py`/`game.py` os carregam, servidor nunca). Ver
> `ARQUITETURA_ONLINE.md` (Decisão 19) pro registro completo da migração
> (mapeamento arquivo-a-arquivo, casos especiais de `__import__` dinâmico, e
> o motivo do nome `release_tools/` em vez de `packaging/`).
>
> `server/`/`client/`/`shared/` (já existiam, não mudaram de lugar) continuam
> sendo a separação PRINCIPAL e mais importante do projeto — as pastas novas
> abaixo só organizam o que antes vivia solto na raiz.

```
rpg_ecs_online/
│
├── main.py, game.py                ← entry points (ficam na raiz por convenção)
├── config.py, paths.py             ← ficam na raiz DE PROPÓSITO: resolvem
│                                      caminho via os.path.dirname(__file__)
│                                      assumindo estar ao lado de assets/maps/
│                                      config.json — mover quebraria isso
│
├── content/                        ← COMPARTILHADO: tabelas de conteúdo do
│   │                                  jogo (dado, sem lógica de sistema)
│   ├── mob_definitions.py          ← MOB_TABLE (raça/classe/loot/abilities)
│   ├── item_table.py               ← ITEMS (catálogo único — loot + loja)
│   ├── loot_tables.py              ← roll_mob_loot()/roll_mob_coins() (só lê item_table/mob_definitions)
│   ├── merchant_data.py            ← SHOPS[shop_id]["stock"]
│   ├── quests_data.py              ← definições de quest
│   ├── talent_data.py              ← TALENTS + CLASS_BUILD_MAP
│   ├── skill_config.py             ← SKILL_CATALOG (fonte única de skill)
│   ├── enemy_abilities_data.py     ← ABILITY_DEFS (poison/bleed/stun de mob)
│   ├── crafting_data.py            ← materiais/receitas de crafting
│   ├── status_effects_data.py      ← definições de buff/debuff
│   └── faction_data.py             ← RELATIONSHIP (facção→facção→tier), get_relationship()
│
├── engine/                         ← COMPARTILHADO: ECS headless (server+client),
│   │                                  ZERO pygame no topo — servidor importa direto
│   ├── world.py                    ← registry ECS (World, get_component, etc.)
│   ├── components.py                ← componentes ECS (dado puro)
│   ├── entity_factory.py           ← criação de entidades (player/mob)
│   ├── core_systems.py             ← apply_effect()/StatusEffectSystem base
│   ├── world_systems.py            ← 14 sistemas ECS de gameplay (EnemyAI,
│   │                                  Combat, TileMovement, TileValidation...)
│   ├── stat_fns.py                 ← add_modifier/enter_combat/etc.
│   ├── stats_system.py             ← CLASS_BASE_STATS, Skill Level (xp/bônus)
│   ├── damage_calculator.py        ← fórmulas de dano, resolve_attack_outcome
│   ├── quest_logic.py              ← apply_event/try_start/complete_quest
│   ├── quest_events.py             ← barramento de eventos de quest
│   ├── save_system.py              ← request_autosave e afins
│   ├── utils.py                    ← snap_to_tile(), bresenham_ray, etc.
│   ├── fx.py                       ← façade de efeitos (FLT/SOUNDS/PROC/WARN) —
│   │                                  no-op no servidor, cliente vincula via bind_client_fx()
│   ├── map_loader.py               ← carrega .csv de mapa em Tilemap
│   ├── tileset.py                  ← Tile/Tilemap, is_solid, etc.
│   └── faction_system.py           ← can_engage()/is_hostile()/get_relationship_between()
│
├── ui/                              ← ONLINE-ONLY na prática: client-only mesmo
│   │                                  quando headless-clean (servidor nunca importa)
│   ├── systems.py                   ← sistemas ECS de UI/render (re-exporta world_systems)
│   ├── skill_handlers.py           ← handler de skill do lado do CLIENTE (botão/predição)
│   ├── spell_system.py             ← SpellCastSystem client-side (cast bar)
│   ├── combat_log.py               ← CombatLog/LOG — histórico da aba "Combate" do chat
│   ├── chat_bubble.py               ← balão de fala acima da cabeça
│   ├── ui_components.py/ui_helpers.py/ui_sizes.py/ui_scale_mixin.py/ui_compare.py
│   ├── fonts.py, sound_manager.py, icon_manager.py, tile_sprite_manager.py, fov.py
│   ├── floating_text.py, effect_animator.py, minimap.py, map_overlay.py
│   ├── map_markers.py               ← MapMarker/collect_markers — pontos de
│   │   interesse do mapa/minimapa (morte, quest givers, treinadores,
│   │   mercadores), consumido por map_overlay.py/minimap.py
│   ├── god_mode.py                  ← editor de nível/level (F10)
│   ├── login_screen.py, char_creation_screen.py, settings_screen.py
│   ├── skill_level_ui.py, talent_system.py, quest_system.py, trainer_system.py,
│   │   crafting_system.py           ← painéis/telas modais (todos pygame)
│
├── debug/                           ← módulos de log de diagnóstico ATIVOS —
│   │                                  importados de verdade por server E client
│   │                                  (gated por flag), não são scripts soltos
│   ├── aoi_debug.py                 ← log de AOI subscription/broadcast
│   └── mob_combat_debug.py          ← MCL — log de combate de mob
│
├── tools/                           ← scripts de dev STANDALONE (zero importador
│   │                                  em runtime — rodados manualmente)
│   ├── check_surfaces.py            ← audita antialiasing/smoothscale no repo
│   ├── png_to_map.py                ← converte PNG de referência em CSV de mapa
│   └── reverb.py                    ← pré-processa reverb em assets de áudio (offline)
│
├── release_tools/                   ← build/empacotamento (NUNCA "packaging/" —
│   │                                  colide com a lib real `packaging` do
│   │                                  PyPI, usada pelo próprio PyInstaller)
│   ├── build_client.ps1             ← builda + copia assets/maps + gera LEIA-ME
│   └── rpg_online_client.spec       ← spec do PyInstaller (usa SPECPATH p/ achar main.py)
│
├── shared/                          ← COMPARTILHADO (sem Pygame, sem state)
│   ├── messages.py                  ← MsgType enum + encode/decode + factories
│   └── constants.py                 ← TICK_RATE, AOI_RADIUS, TILE_SIZE, COMBAT_SYNC_STATS
│
├── server/                          ← ONLINE-ONLY (headless, sem Pygame real)
│   ├── main.py                      ← ponto de entrada: asyncio + WebSocket
│   ├── world_server.py              ← ECS headless: loop de ticks, sistemas, skill pipeline
│   ├── session.py                   ← SessionManager: AOI subscription, dispatch, save
│   ├── auth.py                      ← autenticação SQLite (salt por conta) + persistência
│   ├── log.py                       ← logger do servidor (console + logs/server.log rotativo; RPG_LOG_LEVEL)
│   ├── trade_processor.py           ← TradeProcessorMixin/TradeSession: trade player↔player
│   └── server_death_handler.py      ← PendingDeath: XP, loot, SpawnZone, despawn
│
├── client/                          ← ONLINE-ONLY (cliente de rede — mixins de GameEngine)
│   ├── network.py, network_handlers.py, remote_entity_handlers.py,
│   │   save_sync_handlers.py, inventory_handlers.py, tooltip_handlers.py,
│   │   debug_handlers.py, menu_handlers.py, hotbar_editor_handlers.py,
│   │   habilidades_handlers.py, online_mode_handlers.py, hotbar_handlers.py,
│   │   consumable_bar_handlers.py, hud_handlers.py, modal_stack_handlers.py,
│   │   trade_handlers.py, chat_handlers.py, colors.py
│
├── data/                            ← criada automaticamente
│   └── game.db                      ← banco SQLite (contas + personagens)
│
├── tests/                           ← suíte (servidor + cliente headless)
│   ├── test_server.py               ← suite principal do servidor
│   ├── test_session.py, test_combat.py, test_map_filter.py, ...
│   ├── test_client_ui.py            ← testes de UI do cliente (SDL dummy) — 15/07/2026
│   ├── test_auth_salt.py            ← salt/upgrade de auth (banco temporário)
│   ├── test_save_sanitize.py        ← sanitização de inventário na persistência
│   ├── test_map_services.py         ← resolver por-entidade do _svc
│   ├── test_server_entrypoint.py    ← main.py (server E cliente) sobe como script
│   ├── helpers.py                   ← spawn_player/run_ticks/clear_login_immunity
│   └── diag_*.py                    ← scripts de diagnóstico individuais
│
└── arquitetura/                     ← documentação
    ├── MAPA_PROJETO.md              ← este arquivo
    ├── ARQUITETURA_ONLINE.md        ← decisões, protocolo, fluxo de tick, problemas
    ├── SISTEMAS_ECS.md              ← sistemas offline (referência) + sistemas do servidor
    ├── COMPONENTES_ECS.md           ← componentes ECS
    ├── DADOS_JOGO.md                ← conteúdo do jogo
    ├── PROBLEMAS_ARQUITETURA.md     ← débito técnico
    └── CODE_REVIEW.md, DOCUMENTACAO.md, talent_map.md  ← docs soltos, movidos pra cá (09/07/2026)
```

### Arquivos-chave do branch online (vs. master)

| Arquivo | Tipo | Mudanças principais |
|---------|------|---------------------|
| `server/world_server.py` | ONLINE-ONLY | ECS headless, toda a lógica de servidor |
| `server/session.py` | ONLINE-ONLY | AOI, save merge, handlers WebSocket |
| `server/auth.py` | ONLINE-ONLY | SQLite, `asyncio.get_running_loop()` fix |
| `server/server_death_handler.py` | ONLINE-ONLY | Morte de mobs sem Pygame |
| `shared/messages.py` | ONLINE-ONLY | Protocolo completo |
| `shared/constants.py` | ONLINE-ONLY | `COMBAT_SYNC_STATS`, `AOI_RADIUS` |
| `client/network.py` | ONLINE-ONLY | NetworkClient WebSocket |
| `client/network_handlers.py` | NOVO (ONLINE-ONLY) | `NetworkHandlers`: mixin com dispatcher + 17 `_handle_msg_*`, extraído de `game.py` |
| `client/remote_entity_handlers.py` | NOVO (ONLINE-ONLY) | `RemoteEntityHandlers`: mixin com 17 métodos de spawn/despawn/movimento/sync de mobs e players remotos + `_apply_combat_result`, extraído de `game.py` |
| `client/save_sync_handlers.py` | NOVO (ONLINE-ONLY) | `SaveSyncHandlers`: mixin com 15 métodos de save/load de personagem (`_serialize_item`, `_collect_save_state`, `_send_save_state`, `_restore_save_state`...) e envio de sync ao servidor (talentos, hotbar, stats de combate, loot, engage), extraído de `game.py` |
| `client/inventory_handlers.py` | NOVO (ONLINE-ONLY) | `InventoryHandlers`: mixin com painel de equipamentos/inventário — tecla I (`_equip_item`, `_unequip_slot`, `_handle_inventory_click`, `_use_consumable`, `_draw_inventory_panel`), extraído de `game.py`. Etapa 4.17: relocadas para esta classe (única usuária) as 12 constantes de geometria/cor `_RARITY_COLORS`/`_PANEL_W`/`_PANEL_H`/`_PAD`/`_HEADER_H`/`_EQ_W`/`_EQ_SLOT_H`/`_EQ_ICON`/`_BODY_H`/`_INV_SLOT`/`_INV_COLS`/`_INV_GAP` e o método `_panel_origin` — antes "fornecidos" por `GameEngine` como resíduo de quando o painel era desenhado lá; também removidas 4 constantes mortas (`_STATS_H`, `_SLOT_W`, `_SLOT_H`, `_SLOT_PAD`, zero referências no codebase) que viviam no mesmo bloco residual |
| `client/tooltip_handlers.py` | NOVO (ONLINE-ONLY) | `TooltipHandlers`: mixin com 7 métodos de tooltip — cálculo de dano/alcance e geração/desenho de caixas de tooltip de skills, itens (compare panel) e entidades do mundo (`_player_spell_dmg`, `_player_dmg_range`, `_skill_tooltip_lines`, `_draw_tooltip`, `_flush_tooltip`, `_flush_skill_tooltip`, `_draw_world_tooltip`), extraído de `game.py` |
| `client/debug_handlers.py` | NOVO (ONLINE-ONLY) | `DebugHandlers`: mixin com o modal de debug (F12) — 11 métodos: level up, ouro, troca de mapa, catálogo de itens, roteamento de cliques e desenho do modal + abas Nivel/Itens/Ouro/Mapa (`_debug_levelup`, `_handle_debug_click`, `_debug_add_gold`, `_debug_add_item`, `_debug_open_map`, `_get_debug_item_catalog`, `_draw_debug_modal`, `_draw_debug_tab_nivel`, `_draw_debug_tab_ouro`, `_draw_debug_tab_mapa`, `_draw_debug_tab_itens`), extraído de `game.py`. `_DEBUG_MAP_NAMES` (usado só pela aba Mapa) foi relocado junto, por pertencer exclusivamente a este mixin |
| `client/menu_handlers.py` | NOVO (ONLINE-ONLY) | `MenuHandlers`: mixin com o menu de pausa (ESC) e submenus — 9 métodos: helpers de estilo reutilizados + telas de menu (`_mm_overlay`, `_mm_panel`, `_mm_button`, `_draw_pause_menu`, `_draw_quit_confirm`, `_draw_main_menu`, `_draw_resolution_submenu`, `_draw_interface_submenu`, `_draw_sound_submenu`), extraído de `game.py` como bloco contíguo (divisor + 7 constantes `_MM_*` + métodos) via slice literal de linhas |
| `client/hotbar_editor_handlers.py` | NOVO (ONLINE-ONLY) | `HotbarEditorHandlers`: mixin com o editor de atalhos da hotbar (tecla K) — 2 métodos: tabela de rebind de teclas (menus, slots de habilidade e consumíveis) com captura de tecla e botões Salvar/Fechar (`_close_hotbar_editor`, `_draw_hotbar_editor`) + 2 constantes de geometria `_HBE_*`, extraído de `game.py` como bloco contíguo (divisor + constantes + métodos) via slice literal de linhas |
| `client/habilidades_handlers.py` | NOVO (ONLINE-ONLY) | `HabilidadesHandlers`: mixin com o painel de Habilidades (tecla H) — 3 métodos: lista scrollável de skills aprendidas com drag-and-drop para a hotbar, ghost de drag e tela de loading exibida enquanto aguarda LOGIN_OK do servidor (`_draw_habilidades_panel`, `_draw_loading_screen`, `_draw_hab_drag_ghost`), extraído de `game.py` — extração método-a-método via AST (sem constantes de classe intercaladas), mesmo padrão de `TooltipHandlers`/`DebugHandlers` |
| `client/online_mode_handlers.py` | NOVO (ONLINE-ONLY) | `OnlineModeHandlers`: mixin com o ciclo de vida da conexão online — 6 métodos: abre/mantém conexão com o servidor, processa login, drena mensagens recebidas, resolve dano diferido de Bola de Fogo, envia movimento do jogador e desenha o HUD minimalista de status de conexão (`_connect_online`, `_do_login`, `_process_network`, `_process_bdf_pending`, `_send_player_move`, `_draw_online_hud`), extraído de `game.py` — extração método-a-método via AST (sem constantes de classe intercaladas), mesmo padrão de `TooltipHandlers`/`DebugHandlers`/`HabilidadesHandlers` |
| `client/hotbar_handlers.py` | NOVO (ONLINE-ONLY) | `HotbarHandlers`: mixin com a hotbar de habilidades (slots 1-4) — 5 métodos: bloqueio de skill por talento não alocado, clique em slot da hotbar/barra de consumíveis, vinheta de HP baixo e o desenho completo da hotbar — ícones, cooldown, GCD, custo de fúria, indicador de proc, drag-and-drop com remoção (`_is_talent_locked`, `_handle_hotbar_click`, `_handle_consumable_bar_click`, `_draw_low_hp_vignette`, `_draw_hotbar`) + 5 constantes de classe (`_HB_W`, `_HB_H`, `_HB_ICO`, `_HB_PAD`, `_SKILL_FALLBACK_COLORS`), extraído de `game.py` como bloco contíguo (divisor + constantes + métodos) via slice literal de linhas, mesmo padrão de `MenuHandlers`/`HotbarEditorHandlers`. **Relocou também** `_TALENT_SKILL_REQS` (constante de módulo, lookup reverso skill→talento gerado a partir de `talent_data.TALENTS`) — usada exclusivamente por `_is_talent_locked`/`_draw_hotbar`, movida para o módulo do mixin (não duplicada nem deixada órfã em `game.py`), mesmo princípio de `_DEBUG_MAP_NAMES` (etapa 4.5) |
| `client/consumable_bar_handlers.py` | NOVO (ONLINE-ONLY) | `ConsumableBarHandlers`: mixin com o desenho da barra de consumíveis — 1 método: slots, ícones, cooldown/GCD compartilhado com a hotbar de skills, contagem de stack, atalhos de teclado e drag-and-drop com remoção (`_draw_consumable_bar`), extraído de `game.py` como bloco contíguo único — divisor "Barra de consumíveis" viajou junto com o método (mesmo princípio de `OnlineModeHandlers`/`HabilidadesHandlers`: o título da seção pertence à seção, não ao arquivo de origem) |
| `client/hud_handlers.py` | NOVO (ONLINE-ONLY) | `HudHandlers`: mixin com o HUD principal e a barra de cast — 2 métodos: barras de vida/mana/fúria, ícones de buff/debuff, minimapa, ouro, equipamentos rápidos e talentos alocados (`_draw_hud`), e a barra de cast/canalização exibida no centro inferior da tela durante spells canalizadas (`_draw_cast_bar`), extraído de `game.py` como bloco contíguo único — divisor de uma linha (sem título) viajou junto com a seção, mesmo princípio de `OnlineModeHandlers`/`ConsumableBarHandlers`; extração via slice literal de linhas (métodos separados só por uma linha em branco, sem constantes de classe intercaladas) |
| `client/colors.py` | NOVO (ONLINE-ONLY) | Paleta de cores do HUD (`C_WHITE`, `C_YELLOW`, `C_GREEN`, `C_RED`, `C_GRAY`, `C_CYAN`, `C_ORANGE`) — fonte única para `game.py` e mixins; eliminou definições locais duplicadas que bloqueariam extrações futuras (mesmo princípio da correção SCREEN_WIDTH/HEIGHT, ver `PROBLEMAS_ARQUITETURA.md` §9) |
| `client/modal_stack_handlers.py` | NOVO (ONLINE-ONLY) | `ModalStackHandlers`: mixin com `_modal_registry()` (ordem de prioridade dos ~14 modais), `_topmost_open_modal()`/`_any_modal_open()` e `_close_top_modal()` refatorado — único ponto de verdade reusado pelo ESC e pelo filtro de `systems_events` em `game.py` (ver `PROBLEMAS_ARQUITETURA.md` item IU3) |
| `ui/ui_scale_mixin.py` | NOVO (COMPARTILHADO) | `UIScaleMixin`: dá `self._u(px)` e `self.set_ui_scale(scale)` pra Systems de UI que vivem fora de `GameEngine` (`BlacksmithSystem`, `TrainerSystem`, `ShopSystem`, `LootSystem`, `QuestSystem`/`QuestDialogSystem`/`QuestJournalSystem`, `TalentSystem`, `MapOverlay`, `Minimap`) e por isso não tinham acesso a `self._ui_scale` da engine — cada um criava fontes fixas uma vez e nunca escalava (ver `PROBLEMAS_ARQUITETURA.md` item IU4) |
| `ui/ui_sizes.py` | NOVO (COMPARTILHADO) | `UI`: único lugar com todos os tamanhos de design (escala 1.0) da UI — painéis modais, geometria interna de cada painel, HUD, hotbar, minimapa, tooltip, reservas de área segura, bases de fonte. Cada arquivo de painel mantém sua constante local de mesmo nome, só redirecionada pra cá (`_PANEL_W = UI.INVENTORY_W`) — editar um valor aqui afeta o painel automaticamente (ver `PROBLEMAS_ARQUITETURA.md` item IU4) |
| `engine/core_systems.py` | NOVO (COMPARTILHADO) | `apply_effect()` + `StatusEffectSystem` base sem Pygame; importado por cliente e servidor |
| `game.py` | MODIFICADO | `_use_skill_visual_only` + init/loop/render; rede, entidades remotas, save/sync, inventário, tooltips, modal de debug, menu de pausa, editor de hotbar, painel de habilidades, modo online, hotbar de habilidades, barra de consumíveis e HUD principal+cast bar agora vêm de `NetworkHandlers`/`RemoteEntityHandlers`/`SaveSyncHandlers`/`InventoryHandlers`/`TooltipHandlers`/`DebugHandlers`/`MenuHandlers`/`HotbarEditorHandlers`/`HabilidadesHandlers`/`OnlineModeHandlers`/`HotbarHandlers`/`ConsumableBarHandlers`/`HudHandlers` (ver `client/network_handlers.py`, `client/remote_entity_handlers.py`, `client/save_sync_handlers.py`, `client/inventory_handlers.py`, `client/tooltip_handlers.py`, `client/debug_handlers.py`, `client/menu_handlers.py`, `client/hotbar_editor_handlers.py`, `client/habilidades_handlers.py`, `client/online_mode_handlers.py`, `client/hotbar_handlers.py`, `client/consumable_bar_handlers.py`, `client/hud_handlers.py`); cores do HUD vêm de `client/colors.py` |
| `ui/systems.py` | MODIFICADO | Re-exporta `apply_effect` de `core_systems`; `StatusEffectSystem` subclasse com FLT |
| `ui/skill_handlers.py` | MODIFICADO | `MELEE_RANGE_PX`, `_range_ok()`, pixel-based range |
| `engine/damage_calculator.py` | MODIFICADO | `is_ability` flag no `resolve_attack_outcome` |
| `ui/sound_manager.py` | MODIFICADO | `play_mob_sounds_at()`, `volume_at()`, `play_skill_at()` |
| `engine/components.py` | MODIFICADO | `Skill._server_pending`, `PlayerSkills.GCD_DURATION=0.8` |

---

## Separação de responsabilidades

```
server/world_server.py    → estado canônico do mundo, lógica de jogo
server/session.py         → I/O de rede, distribuição de estado, save
server/server_death_handler.py → morte de mobs (sem Pygame)
shared/messages.py        → contrato de comunicação
shared/constants.py       → constantes sincronizadas
client/network.py         → transporte assíncrono transparente ao game loop
```

**Regra:** `server/` nunca importa Pygame para display/input (SDL dummy é workaround para sistemas herdados — ver A1 em ARQUITETURA_ONLINE.md). `client/` nunca executa lógica de jogo (só renderiza estado recebido).

---

## Padrões do projeto

### Adicionar nova skill
1. `content/skill_config.py` → entrada em `SKILL_CATALOG` com `params: {}`
2. `ui/skill_handlers.py` → `def _skill_<id>(self, skill, combat_stats, combat_state, tile_move)`
3. Se tiver cast time → `ui/spell_system.py` → registrar em `SpellCastSystem._CAST_HANDLERS`
4. Se for desbloqueada por talento → `content/talent_data.py` → `unlocks_skill`
5. Testar no servidor: handler é chamado via `_process_skill_requests`

### Adicionar nova stat ao PLAYER_STAT_SYNC
1. `shared/constants.py` → inserir em `COMBAT_SYNC_STATS` `{chave_cliente: base_attr_cs}`
2. Nenhuma outra mudança necessária — `sync_player_combat_stats` e `_apply_stat_overrides` são genéricos

### Adicionar novo tipo de mensagem
1. `shared/messages.py` → adicionar em `MsgType` + documentar payload na docstring
2. `server/session.py` → handler `async def _handle_*` + entrada em `_handlers`
3. `client/network_handlers.py` → adicionar branch no dispatcher `_handle_net_message` + método `_handle_msg_<tipo>` (mixin `NetworkHandlers`, herdado por `GameEngine`)
4. `ARQUITETURA_ONLINE.md` → atualizar tabela de mensagens
