# Mapa do Projeto — RPG ECS

> Guia rápido para localizar qualquer parte do projeto.
> Atualizado: 2026-05-03

---

## Onde encontrar o quê

| Quero… | Arquivo | Seção |
|--------|---------|-------|
| Criar/modificar uma skill | `skill_config.py` | `SKILL_CATALOG` |
| Implementar lógica de uma skill do guerreiro | `skill_handlers.py` | `def _skill_<id>` |
| Implementar lógica de uma skill do mago | `skill_handlers.py` + `spell_system.py` | `_skill_*` + `_complete_cast` |
| Adicionar talento | `talent_data.py` | `TALENTS` + `CLASS_BUILD_MAP` |
| Aplicar efeito de talento no jogo | `talent_system.py` | `apply_talent_effects()` |
| Criar item/arma | `loot_tables.py` | `_T` dict |
| Adicionar item em drop de mob | `loot_tables.py` | `MOB_LOOT_TABLES` ou `LOOT_TABLES` |
| Criar um mob novo | `mob_definitions.py` | `MOB_TABLE` |
| Definir habilidade de mob | `enemy_abilities_data.py` | `ABILITY_DEFS` + `MOB_ABILITIES` |
| Criar uma quest | `quests_data.py` | `QUESTS` |
| Adicionar tipo de efeito de status | `status_effects_data.py` | `EFFECT_DEFS` |
| Definir atributos base por classe | `stats_system.py` | `CLASS_MELEE_OVERRIDES` |
| Criar receita de crafting | `crafting_data.py` | `RECIPES` |
| Criar loja nova | `merchant_data.py` | `SHOPS` |
| Adicionar um componente ECS | `components.py` | no final da categoria relevante |
| Criar um novo Sistema ECS | `systems.py` (ou módulo próprio) | herdar de `System` |
| Registrar sistema no loop | `game.py` | `_init_systems()` → `self.systems` |
| Modificar fórmula de dano | `damage_calculator.py` | `calculate_base_damage()` |
| Modificar fórmula de acerto/crit | `damage_calculator.py` | `resolve_attack_outcome()` |
| Alterar funções de stat | `stat_fns.py` | `add_modifier`, `remove_modifier`, `enter_combat`, `learn_recipe` |
| Sincronizar velocidade de ataque | `stats_system.py` | `sync_attack_interval(cs, equip)` — só na criação/load |
| Restrições de armadura por classe | `stats_system.py` | `CLASS_ARMOR_ALLOWED` dict |
| Velocidade de ataque base por classe | `stats_system.py` | `CLASS_MELEE_OVERRIDES` dict |
| Criar mapa novo | `maps/*.csv` + `maps/*_entities.json` | ver DOCUMENTACAO.md seção 6 |

---

## Estrutura de arquivos

```
rpg_ecs/
├── main.py                    ← ponto de entrada
├── game.py                    ← GameEngine: loop, HUD, orquestração
├── world.py                   ← registro ECS (entidades + índices)
├── components.py              ← TODOS os componentes de dados (~1100 linhas)
├── systems.py                 ← TODOS os sistemas de lógica (~4600 linhas)
├── entity_factory.py          ← factory functions (create_player, create_enemy…)
│
├── spell_system.py            ← sistemas de magia do mago (8 classes)
├── skill_handlers.py          ← mixin com 20+ handlers de skills
├── skill_config.py            ← SKILL_CATALOG — fonte única de dados de skills
├── stats_system.py            ← XPSystem, DeathRespawnSystem, CLASS_MELEE_OVERRIDES
├── stat_fns.py                ← funções puras: add_modifier, enter_combat, etc.
├── damage_calculator.py       ← matemática de combate (sem state)
│
├── talent_system.py           ← TalentSystem: UI e aplicação de talentos
├── talent_data.py             ← TALENTS, BUILDS, CLASS_BUILD_MAP
│
├── quest_system.py            ← QuestSystem, QuestDialogSystem, QuestJournalSystem
├── quests_data.py             ← QUESTS, ObjectiveDef, QuestReward
├── quest_events.py            ← fire(event_type, **data) — bus de eventos de quest
│
├── loot_tables.py             ← _T (119 itens), LOOT_TABLES, MOB_LOOT_TABLES
├── mob_definitions.py         ← MOB_TABLE (14 tipos de mob)
├── enemy_abilities_data.py    ← ABILITY_DEFS, MOB_ABILITIES
├── crafting_data.py           ← MATERIALS, RECIPES, RECYCLE_TABLE
├── merchant_data.py           ← SHOPS (estoque de NPCs mercadores)
├── status_effects_data.py     ← EFFECT_DEFS (14 efeitos de status)
│
├── save_system.py             ← save/load com thread worker persistente
├── map_loader.py              ← carregamento de CSVs e JSONs de mapa
├── tileset.py                 ← TileType, TILE_MAPPING, OBJECT_SHEET_FAMILIES
│
├── god_mode.py                ← editor in-game (desenvolvimento)
├── char_creation_screen.py    ← tela de criação de personagem
├── map_overlay.py             ← overlay de mapa (M)
├── minimap.py                 ← minimap
├── floating_text.py           ← textos flutuantes de dano/proc
├── combat_log.py              ← LOG (singleton) — mensagens de combate
├── fonts.py                   ← make(size) → pygame.Font
├── icon_manager.py            ← ICONS (singleton) — cache de ícones PNG
├── sound_manager.py           ← SOUNDS (singleton) — áudio com canais
├── paths.py                   ← resource_path (dev + PyInstaller)
├── config.py                  ← leitura/escrita de config.json
├── fov.py                     ← shadowcasting (8 octantes)
├── ui_helpers.py              ← item_tooltip_lines, RARITY_COLORS
├── ui_compare.py              ← painel de comparação de itens
├── png_to_map.py              ← ferramenta: converte PNG em CSV de mapa
│
├── maps/                      ← mapas do jogo
│   ├── map_1.csv              ← mapa de teste
│   ├── map_main.csv           ← mapa principal
│   ├── map_cave_east.csv
│   ├── map_cave_west.csv
│   ├── map_worm_cave.csv
│   └── *_entities.json        ← NPCs, spawns, transições, zonas de áudio
│
├── assets/
│   ├── tiles/                 ← tilesets PNG (TX Tileset Grass, TX Tileset Wall…)
│   ├── icons/                 ← ícones de skills (skill_*.png)
│   └── sounds/sfx/            ← efeitos sonoros (.ogg)
│
└── arquitetura/               ← esta pasta
    ├── MAPA_PROJETO.md        ← este arquivo
    ├── COMPONENTES_ECS.md     ← todos os componentes documentados
    ├── SISTEMAS_ECS.md        ← todos os sistemas + ordem de execução
    ├── DADOS_JOGO.md          ← inventário de conteúdo (skills, itens, mobs…)
    └── PROBLEMAS_ARQUITETURA.md ← análise crítica e débito técnico
```

---

## Padrões do projeto

### Adicionar nova skill
1. `skill_config.py` → entrada em `SKILL_CATALOG` com `params: {}` para todos os valores de gameplay (multiplicadores, raios, durações, percentuais). Campos padrão: `offensive`, `school`, `needs_aoe_target`, `mana_cost_pct`
2. `skill_handlers.py` → `def _skill_<id>(self, skill, combat_stats, combat_state, tile_move)`
3. Se tiver cast time → `spell_system.py` → registrar em `SpellCastSystem._CAST_HANDLERS` dict (mana deduzida aqui, não no handler)
4. Se for desbloquada por talento → `talent_data.py` → `unlocks_skill = "<id>"` (skill já deve estar em `SKILL_CATALOG`)
5. Se render deve aparecer sobre tiles → chamar explicitamente em `game.py` após `render_fog()` (ver padrão da Pirofagia e Calamidade)

### Adicionar novo talento
1. `talent_data.py` → entrada em `TALENTS`
2. `talent_system.py` → reset em `apply_talent_effects()` + linha de aplicação
3. Se efeito comportamental → campo em `CombatStats.components.py`
4. Se lógica na gameplay → leitura do flag em sistema relevante

### Adicionar novo efeito de status
1. `status_effects_data.py` → entrada em `EFFECT_DEFS`
2. `systems.py StatusEffectSystem._apply_tick()` → caso no if/elif
3. `systems.py EnemyAISystem` → caso no bloco de controle de IA (stun/fear/polymorph/disoriented)

### Adicionar novo sistema ECS
1. Criar classe herdando `System` (em `systems.py` ou novo arquivo)
2. Implementar `update(events, dt)` e/ou `render(cam_x, cam_y)`
3. `game.py _init_systems()` → instanciar + adicionar a `self.systems`
4. Se render deve aparecer por cima dos tiles → chamar explicitamente após `tile_render_system.render_fog()`
