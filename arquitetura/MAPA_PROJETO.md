# Mapa do Projeto — RPG ECS Online

> Guia rápido para localizar qualquer parte do projeto.
> Branch: **online** — versão multiplayer em desenvolvimento paralelo ao `master`.
> Última atualização: 2026-05-22

> **ATENÇÃO:** Ler `arquitetura/ARQUITETURA_ONLINE.md` antes de qualquer trabalho neste branch.

---

## Onde encontrar o quê — Online (ONLINE-ONLY)

| Quero… | Arquivo | Seção |
|--------|---------|-------|
| Definir/modificar tipo de mensagem | `shared/messages.py` | `MsgType` enum + docstring do payload |
| Adicionar handler de mensagem no servidor | `server/session.py` | `_handlers` dict + `async def _handle_*` |
| Alterar constante de rede (tick rate, AOI, etc.) | `shared/constants.py` | constante direta |
| Adicionar stat ao PLAYER_STAT_SYNC | `shared/constants.py` | `COMBAT_SYNC_STATS` dict |
| Lógica de autenticação / persistência | `server/auth.py` | `authenticate()`, `save_character()` |
| Loop de ticks / ECS headless | `server/world_server.py` | `WorldServer._tick()` |
| Spawn/despawn de player | `server/world_server.py` | `spawn_player()`, `despawn_player()` |
| Save merge (autoridade por campo) | `server/session.py` | `_build_save_merge()` |
| Processar mortes de mobs no servidor | `server/server_death_handler.py` | `ServerDeathHandler.update()` |
| Gerenciar sessões e broadcast AOI | `server/session.py` | `SessionManager` |
| AOI subscription (known_eids) | `server/session.py` | `_build_update_for_session()` |
| Sincronizar stats de equipamento/buff | `server/world_server.py` | `sync_player_combat_stats()`, `_apply_stat_overrides()` |
| Re-aplicar talentos ao ECS do servidor | `server/world_server.py` | `apply_talent_effects_to_player()` |
| Iniciar o servidor | `server/main.py` | `py -3.10 server/main.py` |
| Conectar cliente ao servidor | `client/network.py` | `NetworkClient` |
| Banco de dados / schema | `data/game.db` (SQLite) | criado por `auth.init_db()` |

## Onde encontrar o quê — Compartilhado (COMPARTILHADO)

| Quero… | Arquivo | Seção |
|--------|---------|-------|
| Definir constantes rede/mundo | `shared/constants.py` | direto |
| Encodar/decodar mensagem | `shared/messages.py` | `encode()`, `decode()` |
| COMBAT_SYNC_STATS (stats sincronizadas) | `shared/constants.py` | `COMBAT_SYNC_STATS` |

## Onde encontrar o quê — Offline/herdado (COMPARTILHADO com cliente online)

| Quero… | Arquivo | Seção |
|--------|---------|-------|
| Criar/modificar uma skill | `skill_config.py` | `SKILL_CATALOG` |
| Implementar handler de skill do guerreiro | `skill_handlers.py` | `_skill_<id>` |
| Range check de skill (pixel-based) | `skill_handlers.py` | `MELEE_RANGE_PX`, `_range_ok()`, `_melee_ok()` |
| Implementar skill do mago | `skill_handlers.py` + `spell_system.py` | `_skill_*` + `_complete_cast` |
| Fórmula de dano + is_ability miss bypass | `damage_calculator.py` | `resolve_attack_outcome(is_ability=)` |
| Funções de stat (modifier, combat) | `stat_fns.py` | `add_modifier`, `enter_combat`, etc. |
| Stats base por classe / attack interval | `stats_system.py` | `CLASS_BASE_STATS`, `sync_attack_interval()` |
| Adicionar talento | `talent_data.py` | `TALENTS` + `CLASS_BUILD_MAP` |
| Efeito de talento no jogo | `talent_system.py` | `apply_talent_effects()` |
| Criar item/arma/arco/aljava | `loot_tables.py` | `_T` dict |
| Adicionar drop de mob | `loot_tables.py` | `MOB_LOOT_TABLES` |
| Criar mob novo | `mob_definitions.py` | `MOB_TABLE` |
| Sons de mob (aggro, death, attack) | `mob_definitions.py` | `"sounds"` dict por mob |
| Sons posicionais online | `sound_manager.py` | `play_mob_sounds_at()`, `volume_at()` |
| Componente ECS | `components.py` | categoria relevante |
| Sistema ECS (offline) | `systems.py` | herdar de `System` |
| Registrar sistema no loop offline | `game.py` | `_init_systems()` → `self.systems` |

---

## Estrutura de arquivos

```
rpg_ecs_online/
│
├── shared/                         ← COMPARTILHADO (sem Pygame, sem state)
│   ├── messages.py                 ← MsgType enum + encode/decode + factories
│   └── constants.py                ← TICK_RATE, AOI_RADIUS, TILE_SIZE, COMBAT_SYNC_STATS
│
├── server/                         ← ONLINE-ONLY (headless, sem Pygame real)
│   ├── main.py                     ← ponto de entrada: asyncio + WebSocket
│   ├── world_server.py             ← ECS headless: loop de ticks, sistemas, skill pipeline
│   ├── session.py                  ← SessionManager: AOI subscription, dispatch, save
│   ├── auth.py                     ← autenticação SQLite + persistência
│   └── server_death_handler.py     ← PendingDeath: XP, loot, SpawnZone, despawn
│
├── client/                         ← ONLINE-ONLY (cliente de rede)
│   ├── network.py                  ← NetworkClient: WebSocket em background thread
│   ├── network_handlers.py         ← NetworkHandlers: mixin com _handle_net_message + _handle_msg_*
│   ├── remote_entity_handlers.py   ← RemoteEntityHandlers: mixin com spawn/move/sync/draw de mobs e players remotos
│   ├── save_sync_handlers.py       ← SaveSyncHandlers: mixin com save/load de personagem + envio de sync ao servidor
│   ├── inventory_handlers.py       ← InventoryHandlers: mixin com painel de equipamentos/inventário (tecla I)
│   ├── tooltip_handlers.py         ← TooltipHandlers: mixin com cálculo de dano/alcance e desenho de tooltips (skills, itens, mundo)
│   ├── debug_handlers.py           ← DebugHandlers: mixin com o modal de debug (F12) — abas Nivel/Itens/Ouro/Mapa
│   ├── menu_handlers.py            ← MenuHandlers: mixin com o menu de pausa (ESC) e submenus (resolução/interface/som)
│   ├── hotbar_editor_handlers.py   ← HotbarEditorHandlers: mixin com o editor de atalhos da hotbar (tecla K)
│   ├── habilidades_handlers.py     ← HabilidadesHandlers: mixin com o painel de Habilidades (H), drag ghost e tela de loading
│   ├── online_mode_handlers.py     ← OnlineModeHandlers: mixin com conexão/login, processamento de rede e HUD de status online
│   ├── hotbar_handlers.py          ← HotbarHandlers: mixin com a hotbar de habilidades (1-4), bloqueio por talento e vinheta de HP baixo
│   ├── consumable_bar_handlers.py  ← ConsumableBarHandlers: mixin com o desenho da barra de consumíveis (slots, ícones, drag-and-drop)
│   ├── hud_handlers.py             ← HudHandlers: mixin com o HUD principal (vida/mana/fúria, buffs, minimapa, ouro) e a barra de cast/canalização
│   ├── modal_stack_handlers.py     ← ModalStackHandlers: mixin com o registro centralizado de prioridade de modais (ModalStack) — único ponto de verdade reusado por ESC e pelo filtro de systems_events
│   └── colors.py                   ← paleta de cores do HUD (C_WHITE/C_YELLOW/...) — fonte única para game.py e mixins
│
├── data/                           ← criada automaticamente
│   └── game.db                     ← banco SQLite (contas + personagens)
│
├── tests/                          ← testes do servidor
│   ├── test_server.py              ← suite principal (47 testes)
│   └── diag_*.py                   ← scripts de diagnóstico individuais
│
├── arquitetura/                    ← documentação
│   ├── MAPA_PROJETO.md             ← este arquivo
│   ├── ARQUITETURA_ONLINE.md       ← decisões, protocolo, fluxo de tick, problemas
│   ├── SISTEMAS_ECS.md             ← sistemas offline (referência) + sistemas do servidor
│   ├── COMPONENTES_ECS.md          ← componentes ECS
│   ├── DADOS_JOGO.md               ← conteúdo do jogo
│   └── PROBLEMAS_ARQUITETURA.md    ← débito técnico
│
└── [demais arquivos]               ← herdados do master (compartilhados com cliente)
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
| `ui_scale_mixin.py` | NOVO (COMPARTILHADO) | `UIScaleMixin`: dá `self._u(px)` e `self.set_ui_scale(scale)` pra Systems de UI que vivem fora de `GameEngine` (`BlacksmithSystem`, `TrainerSystem`, `ShopSystem`, `LootSystem`, `QuestSystem`/`QuestDialogSystem`/`QuestJournalSystem`, `TalentSystem`, `MapOverlay`, `Minimap`) e por isso não tinham acesso a `self._ui_scale` da engine — cada um criava fontes fixas uma vez e nunca escalava (ver `PROBLEMAS_ARQUITETURA.md` item IU4) |
| `ui_sizes.py` | NOVO (COMPARTILHADO) | `UI`: único lugar com todos os tamanhos de design (escala 1.0) da UI — painéis modais, geometria interna de cada painel, HUD, hotbar, minimapa, tooltip, reservas de área segura, bases de fonte. Cada arquivo de painel mantém sua constante local de mesmo nome, só redirecionada pra cá (`_PANEL_W = UI.INVENTORY_W`) — editar um valor aqui afeta o painel automaticamente (ver `PROBLEMAS_ARQUITETURA.md` item IU4) |
| `core_systems.py` | NOVO (COMPARTILHADO) | `apply_effect()` + `StatusEffectSystem` base sem Pygame; importado por cliente e servidor |
| `game.py` | MODIFICADO | `_use_skill_visual_only` + init/loop/render; rede, entidades remotas, save/sync, inventário, tooltips, modal de debug, menu de pausa, editor de hotbar, painel de habilidades, modo online, hotbar de habilidades, barra de consumíveis e HUD principal+cast bar agora vêm de `NetworkHandlers`/`RemoteEntityHandlers`/`SaveSyncHandlers`/`InventoryHandlers`/`TooltipHandlers`/`DebugHandlers`/`MenuHandlers`/`HotbarEditorHandlers`/`HabilidadesHandlers`/`OnlineModeHandlers`/`HotbarHandlers`/`ConsumableBarHandlers`/`HudHandlers` (ver `client/network_handlers.py`, `client/remote_entity_handlers.py`, `client/save_sync_handlers.py`, `client/inventory_handlers.py`, `client/tooltip_handlers.py`, `client/debug_handlers.py`, `client/menu_handlers.py`, `client/hotbar_editor_handlers.py`, `client/habilidades_handlers.py`, `client/online_mode_handlers.py`, `client/hotbar_handlers.py`, `client/consumable_bar_handlers.py`, `client/hud_handlers.py`); cores do HUD vêm de `client/colors.py` |
| `systems.py` | MODIFICADO | Re-exporta `apply_effect` de `core_systems`; `StatusEffectSystem` subclasse com FLT |
| `skill_handlers.py` | MODIFICADO | `MELEE_RANGE_PX`, `_range_ok()`, pixel-based range |
| `damage_calculator.py` | MODIFICADO | `is_ability` flag no `resolve_attack_outcome` |
| `sound_manager.py` | MODIFICADO | `play_mob_sounds_at()`, `volume_at()`, `play_skill_at()` |
| `components.py` | MODIFICADO | `Skill._server_pending`, `PlayerSkills.GCD_DURATION=0.8` |

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
1. `skill_config.py` → entrada em `SKILL_CATALOG` com `params: {}`
2. `skill_handlers.py` → `def _skill_<id>(self, skill, combat_stats, combat_state, tile_move)`
3. Se tiver cast time → `spell_system.py` → registrar em `SpellCastSystem._CAST_HANDLERS`
4. Se for desbloqueada por talento → `talent_data.py` → `unlocks_skill`
5. Testar no servidor: handler é chamado via `_process_skill_requests`

### Adicionar nova stat ao PLAYER_STAT_SYNC
1. `shared/constants.py` → inserir em `COMBAT_SYNC_STATS` `{chave_cliente: base_attr_cs}`
2. Nenhuma outra mudança necessária — `sync_player_combat_stats` e `_apply_stat_overrides` são genéricos

### Adicionar novo tipo de mensagem
1. `shared/messages.py` → adicionar em `MsgType` + documentar payload na docstring
2. `server/session.py` → handler `async def _handle_*` + entrada em `_handlers`
3. `client/network_handlers.py` → adicionar branch no dispatcher `_handle_net_message` + método `_handle_msg_<tipo>` (mixin `NetworkHandlers`, herdado por `GameEngine`)
4. `ARQUITETURA_ONLINE.md` → atualizar tabela de mensagens
