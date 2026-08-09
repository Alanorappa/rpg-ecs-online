# Plano de Ação — Análise e Melhorias do Projeto

> Gerado em 2026-06-01. Análise sistema a sistema + comparação com práticas da indústria.
> Atualizado com achados de inconsistências, duplicações e violações de arquitetura.

---

## 1. Status dos Itens Anteriores

| # | Item | Status |
|---|------|--------|
| 0.1 | Commits pendentes | ✅ Concluído |
| 1.1 | Cooldown server-side | ✅ Concluído (skill_processor.py) |
| 1.2 | Pirofagia online | ✅ Concluído |
| 1.3 | skill_handlers sem pygame no topo | ✅ Concluído |
| 2.1 | Volatile fields no respawn | ✅ Concluído (stats_system._respawn) |
| 2.2 | Imports circulares quebrados | ✅ Concluído (lazy imports em skill_handlers) |
| 2.3 | max_hp cap servidor | ✅ Concluído (session.py) |
| 3.1 | Testes automatizados básicos | ✅ 82 testes em tests/ |
| 3.2 | AOI spatial hashing | ✅ Concluído (utils.SpatialHash) |
| 3.3 | Lag comp com timestamps | ✅ Concluído (skill_processor) |
| 3.4 | world_server refatoração Mixin | ✅ Concluído |
| — | Tiro Múltiplo online | ⏳ Pendente (Arqueiro/Bardo) |

---

## 2. Análise Sistema a Sistema — Achados

### 2.1 Violações de Arquitetura ECS

#### A. CombatStats é um God Component (P4 — ainda aberto)
`components.py:74–321` — 200+ linhas, ~26 flags de talento inline:
- `fire_burns_on_crit`, `fire_mana_discount`, `thermal_shock_enabled`, `pyromania_bonus` etc.
- Mobs carregam todos esses campos sem nunca usá-los (desperdício de memória)
- Solução da indústria (WoW, PoE): dados de talento em componentes separados por build ou via `CombatRuntime`

**Indústria:** Unity DOTS e Flecs usam archetypes — entidades com conjuntos de componentes diferentes ficam em grupos separados na memória. Mobs não teriam as flags de Piromania.

**Proposta:** Extrair `MageStatFlags` e `WarriorStatFlags` como componentes opcionais. Custo médio, impacto na legibilidade e manutenção.

#### B. UIState em components.py — violação client/server ✅ FEITO
`components.py:1232–1252` — `UIState`, `ShopUIState`, `LootUIState` são estado de UI do *cliente* dentro do arquivo de componentes compartilhado. Isso viola o princípio de que `components.py` é usado pelo servidor também.

**Resolvido:** os 3 componentes foram movidos para `ui_components.py` (novo arquivo, sem dependências). `entity_factory.py`, `game.py` e `systems.py` (MerchantSystem/LootSystem) atualizados para importar de lá. `entity_factory.create_player` continua instanciando-os (ainda usado por `world_server.py`, mas são componentes inertes no servidor — nunca lidos por código server-side).

#### C. skill_config.py importa pygame para keybinds ✅ FEITO
`skill_config.py:31–39` — `import pygame` só para `DEFAULT_KEYBINDS = [pygame.K_1, ...]`. O servidor importa `skill_config` (para `SKILL_CATALOG`), carregando pygame desnecessariamente.

**Resolvido:** `DEFAULT_KEYBINDS: list[int] = [49, 50, ..., 48]` — valores inteiros diretos (códigos `pygame.K_1`..`K_0`), com comentário explicando a equivalência. Sem `import pygame` em `skill_config.py`.

#### D. stats_system.py importa pygame sem usar ✅ FEITO
`stats_system.py:8` — `import pygame` no topo, zero usos de `pygame.X` no módulo. O servidor importa `stats_system`.

**Resolvido:** `import pygame` removido de `stats_system.py`.

#### E. TileMovement recebe campos via setattr (duck typing tácito) ✅ FEITO
`skill_processor.py:169–173` injeta `_server_dir_x`, `_server_dir_y`, `_server_aoe_x`, `_server_aoe_y` no dataclass `TileMovement` via atribuição direta. Esses campos não são declarados em `@dataclass TileMovement`.

**Resolvido:** `_server_dir_x/y`, `_server_aoe_x/y` declarados em `components.py:TileMovement` com `= 0.0`. `skill_processor.py` faz atribuição direta a campos declarados (sem `setattr` dinâmico).

---

### 2.2 Duplicação de Código

#### A. _spell_damage duplicado entre spell_system e spell_completion_processor ✅ FEITO
`spell_system.py:40–52` — função `_spell_damage(attacker_id, world, dmg_weapon_pct, sp_coeff)`.
`spell_completion_processor.py:364–375` — método `_server_spell_damage(player_eid, dmg_weapon_pct, sp_coeff)`.

São **idênticos** na lógica. O servidor poderia importar `_spell_damage` de `spell_system`.

**Resolvido:** lógica única em `damage_calculator.spell_damage()`. `spell_system._spell_damage` e `spell_completion_processor._server_spell_damage` agora delegam para lá (`damage_calculator.py:resolve_attack_outcome`/`CRITICAL_DAMAGE_MULTIPLIER` também centralizados no mesmo módulo).

#### B. Volatile fields reset duplicado ✅ FEITO
Campos como `pnq_counter`, `embalo_charges`, `fire_instant_ready` são resetados em lugares diferentes:
- `stats_system._respawn`: reseta a maioria, mas **não pnq_counter nem fatiador_tick**
- `systems.py:1378`: reseta `pnq_counter`
- `skill_handlers.py:566`: reseta `fire_instant_ready`

**Resolvido:** `_VOLATILE_FIELDS` (tupla) + `CharacterStats.reset_volatile()` em `components.py:567–584`, cobrindo `fatiador_tick`, `fire_crit_counter`, `fire_crit_timer` e os demais. Chamado em `stats_system.py:315` (`_respawn`).

#### C. apply_char_stats_to_combat chamado em 9 lugares
Chamado em `game.py` (4×), `world_server.py` (2×), `stats_system.py` (2×), `quest_system.py` (1×). Cada chamada recalcula TODOS os stats do zero. Não existe dirty-flag.

**Indústria:** Dirty-flag pattern — `cs._dirty = True` ao mudar atributo base; `update_if_dirty()` no início do tick. Reduz recálculos desnecessários.

**Prioridade baixa** — o jogo tem poucos jogadores; impacto real só com >50 players.

---

### 2.3 Hardcode e Magic Numbers

#### A. Fórmulas de dano hardcoded nos handlers ✅ FEITO
Algumas skills têm fórmulas dentro dos handlers, não no SKILL_CATALOG:

| Skill | Fórmula hardcoded | Onde |
|-------|------------------|------|
| Pirofagia | `150 + SP * 1.50` | `skill_handlers.py:871` |
| Vitória Iminente | `30%` do max HP | `skill_handlers.py:167` |
| Impacto | `50%` dano base | `skill_handlers.py:180` docstring |

**Resolvido:** `skill_config.py:pirofagia` tem `"base_dmg": 150, "dmg_sp_coeff": 1.50`; `skill_handlers.py` lê via `skill.params.get("base_dmg", 150)` / `get("dmg_sp_coeff", 1.50)`. `vitoria_iminente` tem `"heal_pct": 0.30` e `"damage_multiplier": 2.0` no catálogo (ver C.6).

#### B. Duração de efeitos hardcoded nos handlers ✅ FEITO (CC) — Exaustão também migrada (ver M4)
```python
apply_effect(world, eid, "root", 5.0)          # nova_congelante
apply_effect(world, eid, "polymorph", 6.0)     # polimorfia
apply_effect(world, eid, "disoriented", 3.0)   # pirofagia
```
Essas durações deveriam estar no `SKILL_CATALOG` sob `effect_durations: {"root": 5.0}`.

**Resolvido:** `skill_config.py` tem `"effect_durations"` para `nova_congelante` (`root: 5.0`), `polimorfia` (`polymorph: 6.0`), `pirofagia` (`disoriented: 3.0`) e agora `bola_de_fogo` (`exhaustion: 6.0`, ver M4). Handlers/processador leem via `.get("effect_durations", {}).get(effect, default)`.

---

### 2.4 Side-Channels Frágeis

Comunicação entre camadas via atributos mutáveis injetados:

| Side-channel | Onde | Problema | Status |
|--------------|------|---------|--------|
| `_server_pending_spells` | SkillSystem | Detecta modo servidor via `getattr(self, ...)` — implícito | ✅ FEITO (C.1-mini) — `_server_pending_spells: list \| None = None` declarado como class attr (`skill_handlers.py:108`), 13 ocorrências de `getattr` substituídas por acesso direto |
| `_server_dir_x/y` | TileMovement | Campo não declarado no dataclass | ✅ FEITO (A.4 — campos declarados) |
| `_last_proj_spell_is_crit` | SpellCompletionMixin | Estado volátil entre handler e coletor de resultados | ✅ Substituído por `self._proj_spell_result["is_crit"]` (dict) |
| `_last_proj_lapso_proc` | SpellCompletionMixin | Idem | ✅ Substituído por `self._proj_spell_result["lapso_proc"]` (dict) |
| `_last_warn` | SkillHandlers | Mensagem de erro via atributo mutable | ⏳ Pendente (C.1/M2, `skill_handlers.py:102/104`) |
| `last_client_payload` | Session | Todo o estado de save do cliente em um dict genérico | ⏳ Pendente, baixa prioridade (`server/session.py`) |

**Nota:** `_proj_spell_result` (dict) já é uma melhoria parcial sobre os atributos soltos originais, mas ainda não é o `SpellResult` dataclass tipado proposto — C.1 continua válido para tipar esse dict e para `_server_pending_spells`/`_last_warn`.

**Indústria:** Retorno de valor estruturado (dataclass/namedtuple) ou contexto de execução tipado.

**Fix proposto para `_last_proj_spell_is_crit` etc.:** Criar `SpellResult` dataclass retornado pelos handlers:
```python
@dataclass
class SpellResult:
    is_crit: bool = False
    lapso_proc: dict | None = None
    fire_instant_proc: bool = False
```
Handler retorna `SpellResult` em vez de `bool`. Coletor usa o retorno.

---

### 2.5 game.py God Object (7030 linhas)

`game.py` tem 134 métodos e 7030 linhas com responsabilidades:
- Loop principal (`run`, game loop)
- Todos os handlers de mensagens de rede (700+ linhas inline)
- Gestão de entidades remotas (spawn/despawn/move de mobs e players)
- Save/load
- Desenho de HUD (hotbar, cast bar, stats panel, etc.)
- Sistemas de UI inline

**Indústria (Godot, Unity, Unreal):** O `GameEngine` é apenas orquestrador. Cada responsabilidade tem sua classe. O handling de rede fica em um `NetworkMessageHandler` separado.

**Solução incremental sem reescrita:**
1. ~~Extrair `_handle_net_message` para `client/message_handler.py` (classe `ClientMessageHandler`)~~ — **FEITO**, mas como mixin `NetworkHandlers` em `client/network_handlers.py`, herdado por `GameEngine(NetworkHandlers, ...)` (ver nota de design abaixo), não como classe standalone com delegação.
2. ~~Extrair gestão de entidades remotas para `client/remote_entity_manager.py`~~ — **FEITO**, como mixin `RemoteEntityHandlers` em `client/remote_entity_handlers.py` (17 métodos, ~896 linhas: spawn/despawn/movimento/sync de mobs e players remotos + `_apply_combat_result`), mesmo padrão mixin, mesma verificação byte-idêntica.
2.6. ~~Extrair save/load + sync de estado~~ — **FEITO**, como mixin `SaveSyncHandlers` em `client/save_sync_handlers.py` (15 métodos, ~430 linhas: serialização de itens, save/load de personagem, envio de sync de talentos/hotbar/stats/loot/engage), mesmo padrão mixin, mesma verificação byte-idêntica.
2.7. ~~Extrair painel de inventário (tecla I)~~ — **FEITO**, como mixin `InventoryHandlers` em `client/inventory_handlers.py` (5 métodos, ~447 linhas: `_equip_item`, `_unequip_slot`, `_handle_inventory_click`, `_use_consumable`, `_draw_inventory_panel`), mesmo padrão mixin, mesma verificação byte-idêntica. `_panel_origin` ficou em `GameEngine` por depender do global mutável `SCREEN_WIDTH`/`SCREEN_HEIGHT` (ver `PROBLEMAS_ARQUITETURA.md` §9 — bloqueio descoberto nesta etapa, **resolvido logo a seguir**).
2.8. ~~Extrair tooltips de skills/itens/mundo~~ — **FEITO**, como mixin `TooltipHandlers` em `client/tooltip_handlers.py` (7 métodos, ~398 linhas: `_player_spell_dmg`, `_player_dmg_range`, `_skill_tooltip_lines`, `_draw_tooltip`, `_flush_tooltip`, `_flush_skill_tooltip`, `_draw_world_tooltip`), mesmo padrão mixin, mesma verificação byte-idêntica. As cores do HUD (`C_WHITE`/`C_YELLOW`/`C_GREEN`/`C_RED`/`C_GRAY`/`C_CYAN`/`C_ORANGE`), usadas tanto dentro do grupo extraído quanto em métodos que ficam em `GameEngine` (ex. `_draw_hud`), foram movidas para `client/colors.py` — fonte única importada por `game.py` e pelos mixins, em vez de duplicar valores ou reescrever `C_X` → `self._C_X` dentro dos métodos extraídos. Mesmo princípio de "única fonte de verdade" da correção SCREEN_WIDTH/HEIGHT abaixo; evita repetir esse problema em cada extração futura que toque HUD/hotbar/debug modal.
2.9. ~~Extrair modal de debug (F12)~~ — **FEITO**, como mixin `DebugHandlers` em `client/debug_handlers.py` (11 métodos, ~345 linhas: level up, ouro, troca de mapa, catálogo de itens, clique e desenho do modal + abas Nivel/Itens/Ouro/Mapa — `_debug_levelup`, `_handle_debug_click`, `_debug_add_gold`, `_debug_add_item`, `_debug_open_map`, `_get_debug_item_catalog`, `_draw_debug_modal`, `_draw_debug_tab_nivel`, `_draw_debug_tab_ouro`, `_draw_debug_tab_mapa`, `_draw_debug_tab_itens`), mesmo padrão mixin, mesma verificação byte-idêntica. `_DEBUG_MAP_NAMES` (dict de nomes amigáveis de mapas) tinha sua única referência dentro do grupo extraído (`_draw_debug_tab_mapa`) — em vez de duplicar ou deixar órfão em `game.py`, foi **relocado** junto para `debug_handlers.py`, onde passa a ser dado próprio do mixin que o usa.
2.10. ~~Extrair menu de pausa (ESC) e submenus~~ — **FEITO**, como mixin `MenuHandlers` em `client/menu_handlers.py` (9 métodos, ~307 linhas: helpers de estilo reutilizados + telas — `_mm_overlay`, `_mm_panel`, `_mm_button`, `_draw_pause_menu`, `_draw_quit_confirm`, `_draw_main_menu`, `_draw_resolution_submenu`, `_draw_interface_submenu`, `_draw_sound_submenu`) + 7 constantes de cor `_MM_*`, mesmo padrão mixin. Diferente das extrações anteriores (método-a-método via AST), aqui as constantes de classe `_MM_*` ficam intercaladas entre o divisor de seção e o primeiro método — exigiu **slice literal contíguo de linhas** (`"".join(lines[DIVIDER_START-1:END_BODY])`) em vez de concatenação por método, garantindo byte-identidade trivial do bloco inteiro de uma vez. Verificação byte-idêntica + checagem rigorosa de espaçamento no ponto de junção (ver nota de bug abaixo).
2.11. ~~Extrair editor de hotbar (tecla K)~~ — **FEITO**, como mixin `HotbarEditorHandlers` em `client/hotbar_editor_handlers.py` (2 métodos, ~181 linhas: tabela de rebind de teclas — menus, slots de habilidade e consumíveis — com captura de tecla e botões Salvar/Fechar — `_close_hotbar_editor`, `_draw_hotbar_editor`) + 2 constantes de geometria `_HBE_*`, mesmo padrão mixin de `MenuHandlers` (slice literal contíguo de linhas, pois `_HBE_SZ`/`_HBE_GAP` ficam entre o divisor "Editor da hotbar (K)" e o primeiro método). Verificação byte-idêntica + checagem rigorosa de espaçamento no ponto de junção (string-anchor exato, não regex preguiçoso).
2.12. ~~Extrair painel de Habilidades (tecla H) + drag ghost + tela de loading~~ — **FEITO**, como mixin `HabilidadesHandlers` em `client/habilidades_handlers.py` (3 métodos, ~249 linhas: lista scrollável de skills aprendidas com drag-and-drop para a hotbar, ghost de drag, e tela de loading exibida enquanto aguarda LOGIN_OK do servidor — `_draw_habilidades_panel`, `_draw_loading_screen`, `_draw_hab_drag_ghost`), mesmo padrão mixin de `TooltipHandlers`/`DebugHandlers` (extração método-a-método via AST, sem constantes de classe intercaladas — métodos separados só por uma linha em branco). `_draw_loading_screen` está fisicamente entre os outros dois métodos, sob o mesmo divisor "Painel de Habilidades (H)", apesar de tematicamente pertencer ao fluxo de conexão/online — preservou-se a ordem original do bloco contíguo (extração mecânica, sem reordenação por tema). Verificação byte-idêntica + checagem rigorosa de espaçamento no ponto de junção e entre métodos internos do mixin.
2.13. ~~Extrair ciclo de vida da conexão online (Modo Online)~~ — **FEITO**, como mixin `OnlineModeHandlers` em `client/online_mode_handlers.py` (6 métodos, ~136 linhas: abre/mantém conexão com o servidor, processa login, drena mensagens recebidas, resolve dano diferido de Bola de Fogo, envia movimento do jogador e desenha o HUD minimalista de status de conexão — `_connect_online`, `_do_login`, `_process_network`, `_process_bdf_pending`, `_send_player_move`, `_draw_online_hud`), mesmo padrão mixin de `HabilidadesHandlers`/`TooltipHandlers`/`DebugHandlers` (extração método-a-método via AST, sem constantes de classe intercaladas). Verificação byte-idêntica + checagem rigorosa de espaçamento no ponto de junção e entre métodos internos do mixin.
2.14. ~~Extrair hotbar de habilidades (slots 1-4)~~ — **FEITO**, como mixin `HotbarHandlers` em `client/hotbar_handlers.py` (5 métodos, ~496 linhas: bloqueio de skill por talento não alocado, clique em slot da hotbar/barra de consumíveis, vinheta de HP baixo e desenho completo da hotbar — ícones, cooldown, GCD, custo de fúria, indicador de proc, drag-and-drop com remoção — `_is_talent_locked`, `_handle_hotbar_click`, `_handle_consumable_bar_click`, `_draw_low_hp_vignette`, `_draw_hotbar`) + 5 constantes de classe (`_HB_W`, `_HB_H`, `_HB_ICO`, `_HB_PAD`, `_SKILL_FALLBACK_COLORS`), mesmo padrão mixin de `MenuHandlers`/`HotbarEditorHandlers` (slice literal contíguo de linhas, pois os `_HB_*`/`_SKILL_FALLBACK_COLORS` ficam entre o divisor "HUD / Hotbar de habilidades (1-4)" e o primeiro método). **Relocou também** `_TALENT_SKILL_REQS` — constante de MÓDULO (não de classe) em `game.py:53-61`, lookup reverso skill→talento gerado dinamicamente de `talent_data.TALENTS`, referenciada apenas por bare name (sem `self.`) e usada exclusivamente dentro deste grupo (`_is_talent_locked`, `_draw_hotbar`) — para `client/hotbar_handlers.py` como constante de módulo (incluindo seu comentário explicativo e o import `from talent_data import TALENTS as _TT_DATA`), removida de `game.py` sem deixar órfã nem duplicada — mesmo princípio de relocação de `_DEBUG_MAP_NAMES` (etapa 4.5), adaptado para escopo de módulo. Verificação byte-idêntica (métodos, constantes de classe E o dict relocado) + checagem rigorosa de espaçamento nos DOIS pontos de junção (bloco de classe e bloco de módulo) e entre métodos internos do mixin (comparação byte-a-byte com o original, não suposição de "uma linha em branco" — havia um comentário entre `_handle_consumable_bar_click` e `_draw_low_hp_vignette`).
2.15. ~~Extrair barra de consumíveis~~ — **FEITO**, como mixin `ConsumableBarHandlers` em `client/consumable_bar_handlers.py` (1 método, ~232 linhas: slots, ícones, cooldown/GCD compartilhado com a hotbar de skills, contagem de stack, atalhos de teclado e drag-and-drop com remoção — `_draw_consumable_bar`), extraído como bloco contíguo único — o divisor "Barra de consumíveis" viajou junto com o método para o mixin, mesmo princípio de `OnlineModeHandlers`/`HabilidadesHandlers` (o título da seção pertence à seção, não ao arquivo de origem). Verificação byte-idêntica + checagem rigorosa de espaçamento no ponto de junção (regex string-anchored, considerando o divisor de uma linha + linha em branco antes de `_draw_hud`).
2.16. ~~Extrair HUD principal + barra de cast~~ — **FEITO**, como mixin `HudHandlers` em `client/hud_handlers.py` (2 métodos, ~249 linhas: barras de vida/mana/fúria, ícones de buff/debuff, minimapa, ouro, equipamentos rápidos e talentos alocados — `_draw_hud` —, e a barra de cast/canalização exibida no centro inferior da tela durante spells canalizadas — `_draw_cast_bar`), extraído como bloco contíguo único — o divisor de uma linha (sem título) que precedia `_draw_hud` viajou junto com a seção para o mixin, mesmo princípio de `OnlineModeHandlers`/`ConsumableBarHandlers` (o divisor pertence à seção, não ao arquivo de origem); slice literal de linhas, pois os 2 métodos são separados só por uma linha em branco (sem constantes de classe intercaladas — mesma estrutura de `HabilidadesHandlers`/`OnlineModeHandlers`, mas usando slice contíguo por simplicidade já que não há nada entre eles além de espaço em branco). Verificação byte-idêntica (ambos os métodos) + checagem rigorosa de espaçamento no ponto de junção e entre os dois métodos internos do mixin (comparado byte-a-byte com o original).
2.17. ~~Investigar resíduo final + relocar/remover constantes do painel de inventário~~ — **FEITO**. Após a etapa 4.12, investigação completa do que restava em `GameEngine` (via AST, listando todos os métodos com faixas de linha) confirmou que o grosso das ~2059 linhas remanescentes é núcleo legítimo de init/loop/render (`__init__` 288 linhas, `run` 640 linhas, carregamento de mapas, transições, zoom, fechamento de modais, profiler, save/config, pequenos roteadores de propriedade). Único resíduo de "scaffolding" encontrado: a seção final "Painel de equipamentos + inventário (tecla I)" (game.py:2026-2059) — 16 constantes de classe + o método `_panel_origin`, remanescentes de quando o painel era desenhado em `GameEngine` (os métodos de desenho/clique já viviam em `InventoryHandlers` desde antes da etapa 4). Confirmado via `grep -rn "self\._NOME"` em todo o codebase: 13 itens (`_RARITY_COLORS`, `_PANEL_W`, `_PANEL_H`, `_PAD`, `_HEADER_H`, `_EQ_W`, `_EQ_SLOT_H`, `_EQ_ICON`, `_BODY_H`, `_INV_SLOT`, `_INV_COLS`, `_INV_GAP`, `_panel_origin`) usados EXCLUSIVAMENTE por `InventoryHandlers` via `self.*` (sem colisão com as definições homônimas e independentes de `crafting_system.py`/`trainer_system.py`/`quest_system.py`/`systems.py`); 4 itens (`_STATS_H`, `_SLOT_W`, `_SLOT_H`, `_SLOT_PAD`) com ZERO referências em todo o codebase — confirmadamente código morto. Apresentadas 3 opções ao usuário; escolhida "relocar as 13 usadas + remover as 4 mortas". Diferente das relocações anteriores (`_DEBUG_MAP_NAMES`→`DebugHandlers` na etapa 4.5, `_TALENT_SKILL_REQS`→`hotbar_handlers.py` na etapa 4.10), que moviam dado para um mixin recém-criado, esta foi a **primeira relocação para um mixin PRÉ-EXISTENTE**: exigiu inserção seletiva logo após `class InventoryHandlers:` (mantendo os 13 itens vivos byte-idênticos, descartando os 4 mortos do meio do bloco intercalado), atualização do docstring da classe (que antes documentava essas constantes como "fornecidas" externamente por `GameEngine` — agora descreve que pertencem à própria `InventoryHandlers`, "únicas usuárias quando ainda viviam em GameEngine"), e remoção da seção inteira (divisor + 16 constantes + método) do fim de `game.py`. Verificação byte-idêntica dos 13 itens relocados + confirmação de ausência total dos 4 nomes mortos em ambos os arquivos (regex com `(?<!\w)NOME\b`, pois `_SLOT_H` é substring de `_EQ_SLOT_H`, uma constante viva — comparação literal `in` geraria falso positivo) + checagem de que o corpo pré-existente de `InventoryHandlers` permanece intocado + checagem de espaçamento (bloco inserido e ponto de junção em `game.py`, que agora é o fim físico da classe `GameEngine`). Smoke test confirma `GameEngine` resolve as 13 entidades via MRO (`InventoryHandlers`) e que os 4 nomes mortos levantam `AttributeError` (remoção real, não órfã). `game.py`: 2059 → **2025 linhas** (-34 líquidas: -35 da seção removida +1 nova linha em branco de junção).
3. `game.py` agora herda treze mixins: `class GameEngine(NetworkHandlers, RemoteEntityHandlers, SaveSyncHandlers, InventoryHandlers, TooltipHandlers, DebugHandlers, MenuHandlers, HotbarEditorHandlers, HabilidadesHandlers, OnlineModeHandlers, HotbarHandlers, ConsumableBarHandlers, HudHandlers):` — restam **2025 linhas** (de 7030 originais, ~71% de redução), concentradas quase inteiramente em init/loop/render/helpers de suporte — **Etapa 4 ("game.py volta a ser apenas init+loop+render") considerada concluída**, pendente apenas confirmação final do usuário.

**⚠️ Classe de bug descoberta e corrigida — "linha em branco dupla" na remoção pós-extração:** a fórmula de remoção `lines[:BLANK_BEFORE_DIVIDER-1] + ["\n"] + lines[END_BODY:]`, usada nas etapas 4.4 (`TooltipHandlers`) e 4.5 (`DebugHandlers`), mantém a linha em branco original que sucedia o bloco removido **e** insere uma nova — produzindo duas linhas em branco onde a convenção do projeto exige exatamente uma. Descoberta de forma proativa (não reportada pelo usuário) ao reinspecionar o arquivo antes da etapa 4.6: encontrados DOIS pontos afetados — junção "Menu de pausa"/divisor seguinte (introduzido pela própria extração em curso) e junção `_draw_consumable_bar`/`_draw_hud` (resíduo da etapa 4.4, que o regex de verificação de `_verify_tooltip.py` deixou passar por usar `.*?` "preguiçoso" com `re.S`, casando um trecho distante em vez do ponto de junção real). Ambos corrigidos manualmente; fórmula corrigida para `lines[END_BODY+1:]` (descarta as DUAS linhas em branco originais e insere exatamente uma nova) e aplicada em `_split_menu.py` antes da execução. `_verify_menu.py` passou a checar o espaçamento com correspondência exata de âncora (não regex preguiçoso) — `assert blank_run == "\n"`.

**✅ Bloqueio resolvido — migração SCREEN_WIDTH/SCREEN_HEIGHT → self.screen.get_width/get_height():** os globais mutáveis `SCREEN_WIDTH`/`SCREEN_HEIGHT` (reatribuídos via `global` em `_apply_scale` ao trocar resolução) foram **eliminados** de `game.py`. As ~55 ocorrências (29 + 26) foram substituídas por `self.screen.get_width()`/`self.screen.get_height()` — consulta direta ao `pygame.Surface` ativo, única fonte de verdade, sempre atualizada (já era o padrão usado em parte do código, ex. `game.py:2875`). As declarações `global` e as constantes de módulo foram removidas. Diff confirma que somente as linhas tocando essas variáveis mudaram (zero efeitos colaterais); smoke test confirma que o valor acompanha trocas de resolução em runtime sem nenhuma sincronização manual. Isso desbloqueia a extração mecânica de **qualquer** grupo de métodos de desenho daqui em diante — ver `PROBLEMAS_ARQUITETURA.md` §9 para o diagnóstico e a resolução completos.

**Nota de design (desvio consciente do plano original):** a ideia inicial era uma classe `ClientMessageHandler`/`RemoteEntityManager` standalone que `game.py` instancia e delega chamadas (padrão "indústria" com `self.engine`). Na prática, as ~2000 linhas extraídas têm centenas de referências a `self.xxx` (estado do `GameEngine`: `self.world`, `self._my_eid`, `self._net`, dezenas de caches). Reescrever cada `self.` → `self.engine.` seria arriscado (regex ingênua corrompe strings/comentários; round-trip via AST apaga os comentários "porquê" que o projeto valoriza). Em vez disso, replicou-se o padrão **já comprovado** de `SkillHandlers`/`SkillSystem` (ver `skill_handlers.py`): mixin sem `__init__`, métodos assumem que `self.world`/`self._my_eid`/etc existem (fornecidos pelo host via herança múltipla). Resultado: extração mecânica, zero reescrita de `self.`, verificada como byte-idêntica ao código original em todas as etapas.

**Prioridade média** — não impede features, mas dificulta manutenção.

---

### 2.6 World.get_entities_with — Alocação por Frame

`world.py:48–70` — `get_entities_with` cria uma nova lista Python a cada chamada. Sistemas como `TileMovementSystem`, `EnemyAISystem`, `StatusEffectSystem` chamam isso dezenas de vezes por frame.

**Indústria:** Unity DOTS/Flecs usam iteradores sem alocação e archetypes para acesso O(1). Nossa abordagem com índice invertido é razoável, mas a alocação de lista todo frame é ineficiente.

**Fix simples:** `get_entities_with` pode retornar um generator em vez de list para sistemas que só precisam iterar:
```python
def iter_entities_with(self, *component_types):
    """Versão generator — sem alocação."""
    # ...yield em vez de append
```

**Prioridade baixa** — só importa com >200 entidades ativas.

---

### 2.7 Reconexão de Cliente Ausente ✅ FEITO

`client/network.py` — não há lógica de reconnect. Se a conexão cair (ping timeout, queda de rede), o cliente não tenta reconectar.

**Indústria:** Toda aplicação de rede tem reconnect com backoff exponencial.

**Resolvido:** `client/network.py` implementa `_RECONNECT_INITIAL_DELAY` + flag `reconnecting` + cache de credenciais para relogin automático, com backoff exponencial (1s → 2s → 4s → ... → 30s).

---

### 2.8 Handlers de Skill — Acoplamento Visual no Servidor ✅ ANALISADO — mantém como está

`skill_handlers.py` ainda usa `LOG.add(...)`, `SOUNDS.play_skill(...)`, `FLT.add(...)` em modo servidor. Apesar de serem no-ops pelo `SDL_VIDEODRIVER=dummy`, causam:
- Overhead de chamada desnecessário
- Strings de log sendo construídas mas nunca exibidas
- Dependência implícita de FloatingTextManager, SOUNDS, LOG no servidor

**Decisão:** confirmado que `server/world_server.py:21-24` já seta `SDL_VIDEODRIVER=dummy`/`SDL_AUDIODRIVER=dummy` antes de `pygame.init()` — tradeoff deliberado e já mitigado, mascarando exatamente esse acoplamento. Refatoração para hook de eventos (`_emit_damage`/`_emit_heal`) tocaria ~20 handlers só por ganho cosmético (overhead de no-op é desprezível). Não vale o risco de regressão agora — deixar como está.

---

### 2.9 LOGOUT não tratado no servidor ✅ FEITO

`shared/messages.py:34` define `LOGOUT = "logout"` mas não aparece no `_handlers` dict de `session.py`. O cliente pode enviar LOGOUT para gracefully disconnect mas o servidor ignora.

**Resolvido:** `server/session.py:773` — `MsgType.LOGOUT: _handle_logout` no dict `_handlers`.

---

### 2.10 Skill sem Critério de Crit no Servidor para non-BdF ✅ FEITO

`spell_completion_processor.py:_server_nova_congelante`, `_server_calcinar`, `_server_polimorfia`, `_process_player_channeling` — chamam `_server_apply_magic_damage` sem verificar crit.

Apenas `_server_bola_de_fogo` chama `resolve_attack_outcome` e aplica crit corretamente.

**Resolvido:** `_server_apply_magic_damage(attacker_id, target_id, dmg, is_crit=False, roll_crit=False)` já existia com o parâmetro `roll_crit` (calcula `resolve_attack_outcome` + `CRITICAL_DAMAGE_MULTIPLIER` internamente, seta `self._last_magic_is_crit`). Estado por handler:
- `_server_calcinar` — já chamava com `roll_crit=True` ✅
- `_server_nova_congelante` — **adicionado** `roll_crit=True` na chamada por alvo (linha do loop de `_combat_targets`)
- `_process_player_channeling` (Calamidade Flamejante) — **adicionado** `roll_crit=True`; `"outcome"` no `_combat_this_tick` agora usa `"crit" if self._last_magic_is_crit else "hit"` (era hardcoded `"hit"`)
- `_server_polimorfia` — não aplica dano direto, N/A

**Limitação conhecida (não introduzida por esta correção):** `_last_magic_is_crit` é um único bool de instância: para spells que atingem múltiplos alvos no mesmo tick (Nova Congelante, Calamidade Flamejante), apenas o resultado do **último** alvo processado fica refletido no `outcome` agregado dos demais (mesma limitação genérica do dispatch em `_process_completed_spells`, linhas 118-146). Resolver exigiria por-alvo crit tracking — fora do escopo desta correção pontual; candidato a anotar em `PROBLEMAS_ARQUITETURA.md` se virar bug visível (FLT mostrando "crit" em alvo errado).

---

## 3. Comparação com Indústria — Diagnóstico Rápido

| Aspecto | Nosso projeto | Indústria (WoW, PoE, Tibia) | Gap |
|---------|--------------|------------------------------|-----|
| Tick rate | 30/s | 20–30/s ✅ | Nenhum |
| Protocolo | WebSocket+JSON | UDP+binário ou QUIC | Eficiência (para >100 players) |
| AOI | SpatialHash 15 tiles | Grid/quadtree | OK para scale atual |
| Lag comp | Snapshot por timestamp | Snapshot + replay | Funcional |
| Skill data-driven | Parcial (SKILL_CATALOG) | Total (DBC, JSON) | Fórmulas ainda no código |
| Autoridade servidor | Total em dano/posição | Total | ✅ |
| Persistência | SQLite → PostgreSQL | PostgreSQL/Redis | OK para MVP |
| Reconexão | Sem reconexão | Reconexão automática | Gap real |
| ECS | Inverted index | Archetype (Unity/Flecs) | Aceitável para scale |
| Separação cliente/servidor | Boa (Mixin pattern) | Excelente | Pequenos leaks (visual em handler) |
| Testes | 82 testes | CI/CD com cobertura | OK, pode expandir |

---

## 4. Plano de Ação Priorizado — NOVO

### FASE A — Rápidos (< 1h cada, alto impacto) — ✅ TODOS CONCLUÍDOS

**A.1 — Remover `import pygame` de `stats_system.py`** `[5min]` ✅ FEITO
- `stats_system.py:8` — remover linha. Nenhum uso real.
- Servidor carrega stats_system → não precisa de pygame.

**A.2 — Separar keybinds de skill_config** `[30min]` ✅ FEITO
- `skill_config.py:34–36` — `DEFAULT_KEYBINDS: list[int] = [49, 50, ...]` (códigos diretos, comentário explica equivalência a `pygame.K_1`..`K_0`).
- `import pygame` removido de `skill_config.py`.
- Servidor não importa mais pygame via skill_config.

**A.3 — Mover UIState/ShopUIState/LootUIState para fora de components.py** `[20min]` ✅ FEITO
- Criado `ui_components.py` (sem dependências) com `UIState`, `ShopUIState`, `LootUIState`.
- `components.py:1238–1262` removido. `entity_factory.py`, `game.py`, `systems.py` (`MerchantSystem`/`LootSystem`) importam de `ui_components`.

**A.4 — Declarar campos server-inject em TileMovement** `[15min]` ✅ FEITO
- `components.py:TileMovement:914–917` — campos declarados com `= 0.0`:
  `_server_dir_x: float = 0.0`, `_server_dir_y: float = 0.0`,
  `_server_aoe_x: float = 0.0`, `_server_aoe_y: float = 0.0`
- `setattr` dinâmico eliminado em `skill_processor.py:162–166` (atribuição direta a campo declarado).

**A.5 — LOGOUT handler no servidor** `[10min]` ✅ FEITO
- `server/session.py:773` — `_handle_logout` no `_handlers`.

**A.6 — reset_volatile() em CharacterStats** `[30min]` ✅ FEITO
- `components.py:567–584` — `_VOLATILE_FIELDS` + `reset_volatile()`.
- `stats_system.py:315` (`_respawn`) chama `char_stats.reset_volatile()`.

---

### FASE B — Médio prazo (1–3h cada) — ✅ TODOS CONCLUÍDOS

**B.1 — Extrair `_spell_damage` para módulo shared** `[1h]` ✅ FEITO
- `damage_calculator.py` — `spell_damage()` + `resolve_attack_outcome()`/`CRITICAL_DAMAGE_MULTIPLIER`, fonte única.
- `spell_system.py:33-39` — `_spell_damage` delega para `damage_calculator.spell_damage`.
- `spell_completion_processor.py:481-484` — `_server_spell_damage` delega para o mesmo módulo.

**B.2 — Fórmulas de dano de Pirofagia no SKILL_CATALOG** `[30min]` ✅ FEITO
- `skill_config.py:pirofagia` — `"base_dmg": 150, "dmg_sp_coeff": 1.50`.
- `skill_handlers.py:917,919` — lê via `skill.params.get("base_dmg", 150)` / `get("dmg_sp_coeff", 1.50)`.
- `server/spell_completion_processor.py:_server_calcinar` — já lê do catálogo ✅.

**B.3 — Duração de efeitos no SKILL_CATALOG** `[1h]` ✅ FEITO
- `skill_config.py` — `"effect_durations"` presente em:
  ```python
  "nova_congelante":  {"effect_durations": {"root": 5.0}},
  "pirofagia":        {"effect_durations": {"disoriented": 3.0}},
  "polimorfia":       {"effect_durations": {"polymorph": 6.0}},
  "bola_de_fogo":     {"effect_durations": {"exhaustion": 6.0}},  # adicionado nesta sessão (M4)
  ```
- Handlers/processador leem via `.get("effect_durations", {}).get(eff, default)`.

**B.4 — Crit em todas as spells mágicas do servidor** `[1h]` ✅ FEITO (ver 2.10 para detalhes e limitação conhecida)
- `_server_apply_magic_damage(attacker_id, target_id, dmg, is_crit=False, roll_crit=False)` — já existia.
- `_server_calcinar` já passava `roll_crit=True`. Adicionado nesta sessão: `_server_nova_congelante` e `_process_player_channeling` (Calamidade Flamejante).
- `outcome: "crit"` propagado no `_combat_this_tick`/resultado de spell.

**B.5 — Reconexão automática no NetworkClient** `[1h]` ✅ FEITO
- `client/network.py` — reconnect com backoff exponencial (1s→30s) + relogin automático com credenciais em cache.
- Tela "Reconectando..." em `client/online_mode_handlers.py` (`_draw_online_hud`).

---

### FASE C — Longo prazo (3h+, qualidade de vida)

**C.1 — SpellResult dataclass para handlers** `[2h]` — ⏳ PENDENTE (parcialmente mitigado)
- `_last_proj_spell_is_crit`/`_last_proj_lapso_proc` já não existem — substituídos por `self._proj_spell_result["is_crit"]`/`["lapso_proc"]` (dict).
- ✅ **C.1-mini FEITO**: `_server_pending_spells` agora é class attr declarado (`_server_pending_spells: list | None = None`, `skill_handlers.py:108`); 13 ocorrências de `getattr(self, "_server_pending_spells", None)` substituídas por acesso direto `self._server_pending_spells`.
- Ainda resta: `_last_warn` (`skill_handlers.py:102/104`, ver M2) — já é class attr declarado, mas continua sendo side-channel mutável entre handler e coletor.
- Substituir por `SpellResult` (dataclass tipado) continua válido para `_last_warn` e para `_proj_spell_result`, mas é trabalho de ~2h (~20 assinaturas de handler) — proposto apenas quando um 3º valor precisar trafegar por esse canal.

**C.2 — Extrair ClientMessageHandler de game.py** `[3h]` — ✅ FEITO (via Etapa 4, ver seção 2.5 item 1)
- Realizado como mixin `NetworkHandlers` em `client/network_handlers.py` (não como classe standalone com delegação `self._msg_handler.handle(...)`, ver "Nota de design" na seção 2.5).

**C.3 — RemoteEntityManager** `[2h]` — ✅ FEITO (via Etapa 4, ver seção 2.5 item 2)
- Realizado como mixin `RemoteEntityHandlers` em `client/remote_entity_handlers.py` (17 métodos, ~896 linhas).

**C.4 — CombatStats: extrair flags de talento** `[4h — alto impacto futuro]` — ✅ ANALISADO — decisão: convenção para conteúdo novo, sem migração
- Migrar os ~26 campos existentes (`fire_*`, `pyromania_*`, `embalo_*`, `na_mosca_*`, etc., `components.py:74-221`) é 4h+, ~15+ pontos de leitura, alto risco de regressão em sistemas já estáveis — não compensa agora.
- **Decisão:** adotar componentes opcionais por classe (`*TalentFlags`, ex. `ArcherTalentFlags`) como convenção para **conteúdo novo** — talentos do Arqueiro ainda não portados para online são o candidato natural a já nascer no padrão novo.
- Mobs não recebem esses componentes → reduz tamanho de CombatStats incrementalmente, sem big-bang refactor.
- Handlers de talentos novos checam `self.world.get_component(eid, ArcherTalentFlags)`.

**C.5 — World.iter_entities_with (generator)** `[1h]` — ✅ ANALISADO — descartado, não implementar
- `get_entities_with` (`world.py:48-70`) já usa índice invertido — adequado para o scale atual.
- Maioria dos chamadores itera a lista inteira (sem early-exit) — generator não traria ganho real, e overhead de geração pode superar o custo de montar a lista em contagens atuais de entidades.
- Criar `iter_entities_with` em paralelo a `get_entities_with` introduz API duplicada e risco de drift entre as duas.
- Consistente com a seção 6 ("Não implementar archetype ECS") — descartado permanentemente, não revisitar sem mudança de scale (>500 entidades).

**C.6 — Vitória Iminente e heal % no SKILL_CATALOG** `[30min]` ✅ FEITO
- `skill_config.py:63` — `"vitoria_iminente"` tem `"damage_multiplier": 2.0, "heal_pct": 0.30`.
- Handler lê `skill.params.get("heal_pct", 0.30)`.

---

## 5. Inconsistências Menores a Monitorar

| # | Inconsistência | Arquivo | Status |
|---|----------------|---------|-----------|
| M1 | `apply_effect` usa 5.0 fallback no cliente | ~~`game.py:3282`~~ — referência obsoleta (game.py tem 2025 linhas pós-Etapa 4) | ✅ Resolvido por B.3 (durações vêm do SKILL_CATALOG) |
| M2 | `_last_warn` em SkillHandlers para retornar erro | `skill_handlers.py:102/104` | ⏳ Pendente (C.1) |
| M3 | `StatsUpdate` não é enviado ao fazer level-up pelo servidor em todas as circunstâncias | `world_server.py` | ✅ Resolvido — `_pending_xp_deliveries` (world_server.py:1720-1775) envia hp/hp_max/talent_points no level-up |
| M4 | Duração de `exhaustion` (Exaustão) hardcoded como 6.0 | `spell_completion_processor.py:_server_bola_de_fogo` (1 local — escopo reduzido de "dois lugares" do doc original) | ✅ FEITO — movido para `skill_config.py:bola_de_fogo.effect_durations.exhaustion` (4 ocorrências de `6.0` no método agora leem `_exh_dur`) |
| M5 | Sem `LOGOUT` handler no servidor | `session.py` | ✅ FEITO (A.5) |
| M6 | `fatiador_tick` não resetado em `_respawn` | `stats_system.py:313` | ✅ FEITO (A.6) |
| M7 | `fire_crit_counter` e `fire_crit_timer` resetados em `apply_talent_effects` mas não em `_respawn` | `stats_system.py` | ✅ FEITO (A.6) |
| M8 | Todas as spells mágicas exceto BdF não aplicam crit no servidor | `spell_completion_processor.py` | ✅ FEITO (B.4) |
| M9 | `skill_config.py` importa `pygame` — server carrega pygame via skill_config | `skill_config.py:31` | ✅ FEITO (A.2) |
| M10 | `DEFAULT_KEYBINDS` em skill_config (dados de gameplay misturados com UI) | `skill_config.py:33` | ✅ FEITO (A.2 — agora `list[int]`, comentário documenta a equivalência) |

---

## 6. O que NÃO fazer

- **Não reescrever game.py do zero** — refatoração incremental é mais segura.
- **Não migrar para UDP/binary agora** — WebSocket+JSON funciona bem para o scale atual. Migrar para MessagePack primeiro quando tiver >20 players simultâneos.
- **Não implementar archetype ECS** — nossa World com índice invertido é correta para o scale. Só seria necessário com >500 entidades ativas.
- **Não criar combat_utils.py separado** — os imports circulares já foram resolvidos via lazy imports.

---

## 7. Checklist de Entrega (Novo)

- [x] A.1 — Remover `import pygame` de `stats_system.py`
- [x] A.2 — Separar `DEFAULT_KEYBINDS` de `skill_config.py`
- [x] A.3 — Mover UIState para fora de `components.py` (→ `ui_components.py`)
- [x] A.4 — Declarar campos `_server_dir_x/y/_aoe_x/y` em TileMovement
- [x] A.5 — Handler LOGOUT no servidor
- [x] A.6 — `reset_volatile()` em CharacterStats
- [x] B.1 — `_spell_damage` extraído para `damage_calculator.py`
- [x] B.2 — Fórmula de Pirofagia no SKILL_CATALOG
- [x] B.3 — Durações de efeitos CC no SKILL_CATALOG (incl. exhaustion, ver M4)
- [x] B.4 — Crit em todas as spells mágicas servidor
- [x] B.5 — Reconexão automática no NetworkClient
- [ ] C.1 — SpellResult dataclass (`_server_pending_spells` ✅ FEITO via C.1-mini; resta `_last_warn`)
- [x] C.2 — ClientMessageHandler extraído de game.py (via mixin `NetworkHandlers`, Etapa 4)
- [x] C.3 — RemoteEntityManager extraído de game.py (via mixin `RemoteEntityHandlers`, Etapa 4)
- [x] C.4 — CombatStats: extrair flags de talento — decisão documentada (convenção p/ conteúdo novo, sem migração dos 26 campos existentes)
- [x] C.5 — World.iter_entities_with (generator) — decisão documentada (descartado, não implementar)
- [x] C.6 — Vitória Iminente / heal_pct no SKILL_CATALOG
