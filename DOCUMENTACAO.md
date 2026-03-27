# Documentação do Projeto — RPG ECS

> RPG de estilo Tibia/WoW em Python + Pygame com arquitetura ECS (Entity-Component-System).

---

## Sumário

1. [Visão Geral](#1-visão-geral)
2. [Arquitetura ECS](#2-arquitetura-ecs)
3. [Componentes](#3-componentes)
4. [Sistemas](#4-sistemas)
5. [Entidades e Fábricas](#5-entidades-e-fábricas)
6. [Mapas](#6-mapas)
7. [Mecânicas de Jogo](#7-mecânicas-de-jogo)
8. [Interface do Usuário](#8-interface-do-usuário)
9. [Sistema de Talentos](#9-sistema-de-talentos)
10. [Sistema de Som](#10-sistema-de-som)
11. [Tabelas de Loot](#11-tabelas-de-loot)
12. [Constantes e Configurações](#12-constantes-e-configurações)
13. [Guia de Configuração de Assets](#13-guia-de-configuração-de-assets)
14. [Avaliação Arquitetural e Débito Técnico](#14-avaliação-arquitetural-e-débito-técnico)

---

## 1. Visão Geral

| Propriedade      | Valor                                     |
|------------------|-------------------------------------------|
| Linguagem        | Python 3.9+                               |
| Biblioteca       | Pygame                                    |
| Resolução interna| 1280 × 720 pixels                         |
| Escala           | Configurável (1.0×, 1.25×, 1.5×) via settings |
| FPS alvo         | 60                                        |
| Tamanho do tile  | 32 × 32 pixels                            |
| Arquitetura      | ECS puro (sem herança de objetos de jogo) |
| Gráficos         | Retângulos coloridos (sem sprites ainda)  |

### Arquivos principais

| Arquivo              | Responsabilidade                                    |
|----------------------|-----------------------------------------------------|
| `world.py`           | Registro ECS (entidades + componentes)              |
| `components.py`      | Todos os componentes de dados                       |
| `systems.py`         | Todos os sistemas de lógica                         |
| `stats_system.py`    | Progressão, XP, morte/respawn                       |
| `entity_factory.py`  | Fábricas de entidades                               |
| `game.py`            | GameEngine, loop principal, HUD                     |
| `talent_system.py`   | Sistema de talentos — árvore, UI, aplicação         |
| `talent_data.py`     | Definições de todos os talentos (dados)             |
| `sound_manager.py`   | Sistema de áudio com suporte a contexto de caverna  |
| `floating_text.py`   | Textos flutuantes de dano + rastro de dash          |
| `tileset.py`         | Definições de tiles                                 |
| `map_loader.py`      | Carregamento de mapas CSV e JSON de entidades       |
| `loot_tables.py`     | Tabelas de drop e fórmulas de loot                  |
| `icon_manager.py`    | Cache de ícones PNG                                 |
| `ui_compare.py`      | Painel de comparação de itens                       |
| `combat_log.py`      | Log de mensagens de combate                         |
| `settings_screen.py` | Tela de configurações (escala de resolução)         |
| `config.py`          | Leitura/escrita de config.json                      |
| `paths.py`           | Resolução de caminhos (dev + PyInstaller)           |
| `main.py`            | Ponto de entrada                                    |

---

## 2. Arquitetura ECS

O padrão ECS separa dados de lógica:

- **Entidade** → número inteiro (`int`) que serve apenas como ID
- **Componente** → objeto de dados puro sem lógica (`@dataclass` ou classe simples)
- **Sistema** → lógica que opera sobre entidades que possuem determinados componentes

### `world.py` — Registro ECS

| Método                                      | O que faz                                              |
|---------------------------------------------|--------------------------------------------------------|
| `create_entity() -> int`                    | Cria nova entidade, retorna ID                         |
| `add_component(entity_id, component)`       | Associa componente à entidade                          |
| `get_component(entity_id, tipo) -> obj`     | Retorna componente pelo tipo ou None                   |
| `get_entities_with(*tipos) -> list`         | Consulta entidades por assinatura de componentes       |
| `remove_entity(entity_id)`                  | Remove entidade e todos os seus componentes            |
| `remove_component(entity_id, tipo)`         | Remove um componente específico                        |

### Base dos Sistemas

```python
class System:
    def update(self, events: list = None, dt: float = 0) -> None: ...
    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None: ...
```

---

## 3. Componentes

> Todos os componentes ficam em `components.py`. São dados puros — sem lógica de jogo.

---

### `Position`
Posição atual no mundo em pixels.

| Campo            | Tipo    | Descrição                       |
|------------------|---------|---------------------------------|
| `x, y`           | `float` | Posição atual (pixels)          |
| `prev_x, prev_y` | `float` | Posição no frame anterior       |

---

### `Renderable`
Aparência visual da entidade.

| Campo    | Tipo    | Descrição              |
|----------|---------|------------------------|
| `color`  | `tuple` | RGB, ex: `(255, 0, 0)` |
| `width`  | `int`   | Largura em pixels      |
| `height` | `int`   | Altura em pixels       |

---

### `TileMovement`
Controla interpolação suave de tile para tile.

| Campo              | Tipo    | Descrição                        |
|--------------------|---------|----------------------------------|
| `current_tile_x/y` | `int`   | Posição atual na grade           |
| `target_tile_x/y`  | `int`   | Destino na grade                 |
| `start_pixel_x/y`  | `float` | Ponto inicial da animação        |
| `target_pixel_x/y` | `float` | Ponto final da animação          |
| `progress`         | `float` | Progresso 0.0 → 1.0              |
| `move_duration`    | `float` | Duração do movimento em segundos |
| `is_moving`        | `bool`  | Em movimento?                    |
| `slow_timer`       | `float` | Duração restante do debuff slow  |
| `slow_mult`        | `float` | Multiplicador de velocidade      |
| `is_dash`          | `bool`  | True durante dash do Interceptar |

---

### `CombatStats`
Todos os atributos de combate.

| Atributo base          | Padrão | Derivado de          |
|------------------------|--------|----------------------|
| `base_stamina`         | 10     | Vitality             |
| `base_armor`           | 0      | Defense              |
| `base_attack_power`    | 5      | Strength             |
| `base_spell_power`     | 0      | Intelligence         |
| `base_crit_rating`     | 0.05   | Agility              |
| `base_haste_rating`    | 0.0    | —                    |
| `base_physical_damage` | 1      | Bônus fixo de dano   |
| `base_magical_damage`  | 0      | Bônus fixo mágico    |
| `base_attack_interval` | 3.5s   | Velocidade de ataque |
| `parry_rating`         | 0.0    | Rating (20 = 1%)     |
| `dodge_rating`         | 0.0    | Rating (20 = 1%)     |
| `hit_rating`           | 0.0    | Rating (20 = 1%)     |
| `block_rating`         | 0.0    | Rating (20 = 1%)     |

| Atributo efetivo     | Cálculo                                     |
|----------------------|---------------------------------------------|
| `max_hp`             | `stamina * 10`                              |
| `attack_interval`    | `base_attack_interval - haste / 10` (mín 0.5s) |
| Redução de armadura  | `dano * (100 / (100 + armor))`              |
| Dano crítico         | `2.0 × dano normal`                         |

---

### `CharacterStats`
Atributos de progressão do personagem.

| Campo                    | Descrição                                        |
|--------------------------|--------------------------------------------------|
| `strength/intelligence/agility/vitality/defense` | Atributos base do personagem |
| `level`                  | Nível atual                                      |
| `current_xp`             | XP acumulado no nível atual                      |
| `xp_to_next_level`       | XP necessário para próximo nível                 |
| `pending_stat_points`    | Pontos aguardando distribuição                   |
| `rage / max_rage`        | Recurso de habilidades (max 100)                 |
| `free_executar_charges`  | Cargas livres de Executar (proc Assassino)       |
| `embalo_charges`         | Cargas de Embalo (proc por crítico)              |
| `spawn_tile_x/y`         | Tile de respawn                                  |

- `xp_for_level(level) = 100 × level^1.5`
- A cada level-up: +1 VIT, +1 STR, +1 AGI, +1 INT, +2 DEF

---

### `PermanentStats`
Bônus acumulados que sobrevivem à morte (mecânica roguelike).

| Campo          | Descrição                             |
|----------------|---------------------------------------|
| `strength`     | Força permanente acumulada            |
| `intelligence` | Inteligência permanente acumulada     |
| `agility`      | Agilidade permanente acumulada        |
| `vitality`     | Vitalidade permanente acumulada       |
| `defense`      | Defesa permanente acumulada           |

---

### `StatusEffects`
Efeitos de status temporários sobre a entidade.

| Campo        | Tipo    | Efeito                                               |
|--------------|---------|------------------------------------------------------|
| `stun_timer` | `float` | Imóvel e sem ação enquanto > 0                       |
| `fear_timer` | `float` | Foge do player, não ataca enquanto > 0               |
| `slow_timer` | `float` | Velocidade reduzida (via TileMovement.slow_mult)     |

**Origem dos efeitos:**
- **Stun**: Talento _Alvo Confirmado_ ao usar Interceptar (0.3s por ponto)
- **Fear**: Talento _Horrorizante_ se Executar não matar (1s fixo)
- **Slow**: Talento _Golpe Debilitante_ (ativado via habilidade de talento)

---

### `TalentTree`
Pontos alocados na árvore de talentos do jogador.

| Campo       | Tipo            | Descrição                                    |
|-------------|-----------------|----------------------------------------------|
| `allocated` | `dict[str, int]`| Mapeamento `talent_id → pontos alocados`     |
| `points`    | `int`           | Pontos disponíveis para gastar               |

---

### `SpawnZone`
Define uma zona de respawn de inimigos no mapa.

| Campo            | Descrição                                      |
|------------------|------------------------------------------------|
| `max_count`      | Máximo de mobs simultâneos na zona             |
| `cooldown`       | Tempo entre respawns individuais (segundos)    |
| `enemy_type`     | `"melee"` ou `"ranged"`                        |
| `enemy_tier`     | `"normal"`, `"elite"`, `"rare"`, `"boss"`      |
| `level_min/max`  | Faixa de nível dos mobs spawned                |
| `pending_spawns` | Contador de respawns aguardando cooldown       |

**Comportamento:**
- Ao iniciar o jogo: todos os slots preenchidos imediatamente
- Após morte de mob: incrementa `pending_spawns`; cada cooldown respawna um mob

---

### `AIControlled`
Estado da IA inimiga.

| Campo    | Valores possíveis                                                             |
|----------|-------------------------------------------------------------------------------|
| `state`  | `"IDLE"`, `"CHASING"`, `"ATTACKING"`, `"BLOCKED_BY_PLAYER"`, `"KITING"`, `"RETURNING"`, `"FLEEING"` |

- `"FLEEING"` — estado de medo; mob corre do player sem atacar

---

### `Skill`
Habilidade ativa da hotbar.

| Campo              | Descrição                                         |
|--------------------|---------------------------------------------------|
| `name`             | Nome da habilidade                                |
| `description`      | Texto descritivo                                  |
| `cooldown`         | Duração da recarga em segundos                    |
| `current_cooldown` | Tempo restante até poder usar                     |
| `skill_id`         | Identificador único (ex: `"interceptar"`)         |
| `icon_name`        | Nome do arquivo PNG sem extensão (`skill_<id>`)   |
| `max_charges`      | Máximo de cargas (para skills charge-based)       |

**Habilidades da hotbar:**

| Tecla | Skill ID           | Nome              | Efeito                                        | CD    |
|-------|--------------------|-------------------|-----------------------------------------------|-------|
| `1`   | `golpe_poderoso`   | Golpe Poderoso    | 3× dano ao alvo adjacente (15 raiva)          | 8s    |
| `2`   | `cura`             | Cura              | Restaura 30% do HP máximo                     | 15s   |
| `3`   | `impacto`          | Impacto           | Dano da arma + 50% AP a todos em raio 3 tiles | 12s   |
| `4`   | `executar`         | Executar          | 5× dano se alvo < 30% HP (10 raiva)           | 5s    |
| `5`   | `vitoria_iminente` | Vitória Iminente  | Gera 20 raiva ao matar inimigo                | —     |
| `6`   | `interceptar`      | Interceptar       | Dash animado até alvo (2–6 tiles)             | 15s   |

**Skills desbloqueadas por talento:**

| Talento               | Skill ID             | Nome                 |
|-----------------------|----------------------|----------------------|
| `cav_golpe_debilitante` | `golpe_debilitante` | Golpe Debilitante    |
| `cav_punho_queixo`    | `punho_queixo`       | Punho no Queixo      |
| `cav_fatiador_corpos` | `fatiador_corpos`    | Fatiador de Corpos   |

---

### `Item`, `Equipment`, `Inventory`, `Wallet`, `Corpse`, `Projectile`
*(sem alterações em relação à versão anterior — ver seção 11.)*

---

### `Tilemap`, `Camera`, `DetectionRadius`, `XPReward`
*(sem alterações)*

---

## 4. Sistemas

### Ordem de execução (cada frame)

```
TileValidation → MouseTargeting → LootSystem → PlayerInput → SkillSystem →
EnemyAI → ProjectileSystem → CorpseSystem → MobRespawnSystem → SpawnZoneSystem →
XPSystem → StatDistribution → DeathRespawn → CombatState → TileMovement → Camera →
[Render]: TileRender → DashTrail → RenderSystem → ProjectileSystem.render →
FLT.render → HUD → Hotbar → Modais
```

---

### `CombatSystem`
Cálculo de dano, redução de armadura e morte.

**Tipos de dano:**
- `"physical"` — `attack_power + physical_damage`, sujeito a armadura, crit, miss, parry, dodge
- `"magical"` — `spell_power + magical_damage`, sujeito a armadura, crit
- `"physical_fixed"` — só usa `base_ability_damage`; sujeito a armadura, crit, miss, parry, dodge mas **não** soma AP/weapon automaticamente

**Fórmula geral:**
```
dano_final   = dano × multiplicador × (2.0 se crítico)
dano_real    = dano_final × (100 / (100 + armor))
```

**Outcomes possíveis:** `crit`, `hit`, `block`, `miss`, `dodge`, `parry`

---

### `EnemyAISystem`
Máquina de estados com 7 comportamentos.

| Estado               | Comportamento                                            |
|----------------------|----------------------------------------------------------|
| `IDLE`               | Parado; verifica detecção do jogador                     |
| `CHASING`            | Caminha em direção ao jogador via A*                     |
| `ATTACKING`          | No alcance; ataca (melee ou projétil)                    |
| `KITING`             | Recua para manter distância mínima (ranged)              |
| `RETURNING`          | Volta ao ponto de spawn                                  |
| `BLOCKED_BY_PLAYER`  | Caminho obstruído; aguarda                               |
| `FLEEING`            | Corre do player (efeito de medo); não ataca              |

- Transição `IDLE → CHASING` dispara som `mob_aggro`
- Line-of-sight via Bresenham; recálculo de caminho a cada 0.3s

---

### `SkillSystem`
Ativa habilidades da hotbar (teclas 1–9).

- GCD universal: todas as skills compartilham o mesmo global cooldown
- Habilidade de channeling (`fatiador_corpos`): bloqueia todas as outras skills enquanto ativa
- Ícones: `skill_key_by_name(skill.icon_name)` — cada skill tem `icon_name = "skill_<id>"`

---

### `TileMovementSystem`
Interpola suavemente a posição entre tiles.

- Emite `DASH_TRAIL.emit()` a cada frame quando `tile_movement.is_dash == True`
- Reseta `is_dash = False` ao terminar o movimento

---

### `SpawnZoneSystem`
Gerencia zonas de respawn definidas no JSON de entidades.

- **Preenchimento inicial**: todos os mobs spawnam de uma vez ao carregar o mapa
- **Pós-morte**: `pending_spawns` conta mobs mortos; a cada `cooldown`, um mob é respawnado
- Timer individual: o respawn começa a contar quando o mob morre (não há respawn em lote)

---

### `StatusEffectSystem` *(integrado no EnemyAISystem)*
Gerencia efeitos temporários sobre mobs.

- Decrementa `stun_timer` e `fear_timer` a cada frame
- Stun: bloqueia `state = "ATTACKING"` e movimento
- Fear: força `state = "FLEEING"` — mob corre do player sem atacar

---

### `TalentSystem` *(talent_system.py)*
Ver [seção 9](#9-sistema-de-talentos).

---

### Demais sistemas
`PathfindingSystem`, `TileValidationSystem`, `MouseTargetingSystem`, `ProjectileSystem`,
`CorpseSystem`, `LootSystem`, `ShopSystem`, `ConsumableSystem`, `MobRespawnSystem`,
`CameraSystem`, `TileRenderSystem`, `RenderSystem`, `XPSystem`, `StatDistributionSystem`,
`DeathRespawnSystem`, `CombatStateSystem` — comportamento inalterado em relação à versão anterior.

---

## 5. Entidades e Fábricas

### `create_player`
Componentes: `Position`, `Renderable`, `PlayerControlled`, `Collider`, `TileMovement`,
`CombatStats`, `CharacterStats`, `PermanentStats`, `PlayerAutoMove`, `Equipment`,
`Inventory` (20 slots), `PlayerSkills`, `Wallet`, `CombatState`, `TalentTree`, `StatusEffects`

### `create_enemy`
Escalamento por nível (acima do nível 1):
- +5 stamina, +2 AP, +2 spell_power, +4 armor, +0.01 crit por nível
- XP: `level × 15 × cfg["xp"]`

### `create_spawn_zone`
Cria entidade de zona com componente `SpawnZone` e `SpawnZoneOwner` nos mobs gerados.

---

## 6. Mapas

### Formato CSV (`maps/`)

| Caractere | Tile / Spawn                              |
|-----------|-------------------------------------------|
| `_` ou `.`| Chão (transitável)                        |
| `#`       | Parede (sólida)                           |
| `G`       | Grama (transitável)                       |
| `O`       | Portal (transição de zona)                |
| `P`       | Spawn do jogador                          |
| `E`       | Inimigo melee normal                      |
| `R`       | Inimigo ranged normal                     |
| `A`       | Inimigo melee elite                       |
| `T`       | Inimigo ranged elite                      |
| `M`       | Inimigo melee raro                        |
| `X`       | Inimigo ranged raro                       |
| `B`       | Inimigo melee boss                        |

### Mapas ativos

| Arquivo                    | Notas                              |
|----------------------------|------------------------------------|
| `maps/map_1.csv`           | Mapa inicial (superfície)          |
| `maps/map_cave_east.csv`   | Caverna leste                      |
| `maps/map_cave_west.csv`   | Caverna oeste                      |

Arquivos `_entities.json` paired com cada CSV definem zonas de spawn e NPCs.

**Contexto acústico:** mapas com `"cave"` no nome ativam eco dinâmico no SoundManager.

---

## 7. Mecânicas de Jogo

### Combate

- **Auto-ataque**: ativado ao selecionar inimigo com clique direito
- **Ataque manual**: SPACE
- **Habilidades**: teclas 1–9
- **Projéteis**: inimigos ranged disparam quando há linha de visão
- **Corpo-a-corpo**: requer tile adjacente (distância de Chebyshev ≤ 1)
- **Dash (Interceptar)**: alcance 2–6 tiles, animado, deixa rastro vermelho

### Progressão

```
Nível N → XP necessário: 100 × N^1.5

Level-up automático: +1 VIT, +1 STR, +1 AGI, +1 INT, +2 DEF
                     +1 ponto de talento
                     +1 ponto de atributo para distribuir (F1–F5)

Fórmulas de stat para combate:
  base_stamina      = 5 + (STR + STR_perm) × 5
  base_armor        = (DEF + DEF_perm) × 2
  base_attack_power = 5 + (STR + STR_perm) × 2
  base_spell_power  = (INT + INT_perm) × 2
  base_crit_rating  = 0.10 + (AGI + AGI_perm) × 0.01
```

### Morte

- Ao morrer, o jogador é reposicionado ao tile de spawn **no mesmo mapa**
- Nível, talentos, atributos, equipamentos e inventário são **preservados**
- `free_executar_charges` e `embalo_charges` são zerados

### SpawnZone

- Preenchimento inicial: todos os mobs aparecem ao mesmo tempo
- Após cada morte: o timer de respawn começa individualmente para aquele slot
- Respawns são distribuídos ao longo do tempo (não em batch)

### Efeitos de Status

| Efeito | Duração | Origem                        | Efeito no mob             |
|--------|---------|-------------------------------|---------------------------|
| Stun   | 0.3s/pt | Interceptar + Alvo Confirmado | Imóvel, sem ataque        |
| Fear   | 1.0s    | Executar + Horrorizante       | Foge do player, sem ataque|
| Slow   | variável| Golpe Debilitante             | Velocidade reduzida       |

---

## 8. Interface do Usuário

### Atalhos de teclado

| Tecla   | Ação                                              |
|---------|---------------------------------------------------|
| W/A/S/D | Movimento                                         |
| SPACE   | Atacar alvo ou selecionar mais próximo            |
| 1–9     | Usar habilidade da hotbar                         |
| I       | Abrir/fechar inventário                           |
| T       | Abrir/fechar árvore de talentos                   |
| M       | Abrir/fechar mapa                                 |
| F1–F5   | Distribuir ponto de atributo pendente             |
| ESC     | Fechar modal aberto / abrir menu de pausa         |
| F12     | Modal de debug (level-up instantâneo)             |

### HUD (canto superior esquerdo)

- **Barra de HP** — vermelho; HP atual / máximo
- **Barra de Raiva** — laranja; recurso de habilidades
- **Barra de XP** — ciano; nível + progresso
- **Atributos** — STR / INT / AGI / VIT / DEF
- **Ratings** — `Crit: X%  Aparo: X%  Esquiva: X%`
- **Ouro** — quantidade

### Hotbar (centro inferior)

- Slots com ícones PNG (`assets/icons/skill_<id>.png`)
- Overlay de cooldown (cinza escuro proporcional)
- Overlay GCD (cinza claro universal entre skills)
- Overlay vermelho em todos os slots durante channeling
- Proc glow (borda colorida) quando skill está com proc ativo:
  - Executar: brilha quando alvo < 30% HP
  - Executar: brilha em verde quando `free_executar_charges > 0` (proc Assassino)
  - Golpe Poderoso: brilha quando `embalo_charges > 0` (proc Embalo)

### Modais

| Modal            | Tecla | Descrição                                     |
|------------------|-------|-----------------------------------------------|
| Inventário       | I     | Equipamentos + grade de itens + stats totais  |
| Árvore de talentos | T   | Árvore com preview de efeito por ponto        |
| Mapa             | M     | Overlay do mapa atual                         |
| Loot             | RMB   | Abre ao clicar com botão direito em cadáver   |
| Pausa            | ESC   | Opções + Quit                                 |
| Debug            | F12   | Level-up instantâneo para testes              |

### Tela de Configurações (settings_screen.py)

- Seleção de escala de renderização: 1.0×, 1.25×, 1.5×
- Renderização interna em 1280×720; janela escala para resolução escolhida
- Mouse corrigido automaticamente para o espaço interno
- Configuração salva em `config.json`

### Textos Flutuantes (floating_text.py)

- `FLT` — números de dano e procs acima das entidades
- `DASH_TRAIL` — rastro vermelho do Interceptar, emitido frame a frame durante o dash

---

## 9. Sistema de Talentos

### Arquivos

| Arquivo          | Responsabilidade                                   |
|------------------|----------------------------------------------------|
| `talent_data.py` | Dados de todos os talentos (TALENTS dict)          |
| `talent_system.py` | TalentSystem — UI, alocação, aplicação de efeitos|

### Estrutura de um talento (`talent_data.py`)

```python
{
    "id":          "cav_interceptar",
    "name":        "Interceptar",
    "description": "...",
    "max_points":  1,
    "requires":    ["cav_outro"],      # pré-requisitos
    "effects":     [{"attr": "...", "value": ...}],    # efeitos passivos
    "unlocks_skill": "interceptar",    # skill desbloqueada (opcional)
    "behavioral":  True,               # efeito especial (sem attr fixo)
}
```

### Exibição da descrição

- 0 pontos alocados → mostra valor com **1 ponto** (preview do primeiro ponto)
- N pontos → mostra valor com **N+1 pontos** (preview do próximo)
- Máximo → mostra valor do nível máximo

### Árvore Cavaleiro (prefixo `cav_`)

| ID                    | Nome               | Efeito principal                                        |
|-----------------------|--------------------|-------------------------------------------------------- |
| `cav_interceptar`     | Interceptar        | Desbloqueia skill Interceptar                           |
| `cav_vontade`         | Vontade            | +10 raiva ao usar Interceptar                           |
| `cav_sede_batalha`    | Sede de Batalha    | Reduz CD do Interceptar em 2s/pt                        |
| `cav_alvo_confirmado` | Alvo Confirmado    | Interceptar atordoa por 0.3s/pt                         |
| `cav_reflexos`        | Reflexos Apurados  | +20 parry_rating/pt (20 = 1%)                           |
| `cav_embalo`          | Embalo             | Críticos geram 1 carga; consome ao usar Golpe Poderoso (+10%/pt dmg) |
| `cav_golpe_debilitante` | Golpe Debilitante | Desbloqueia skill; aplica slow no alvo (5 raiva)       |
| `cav_veterano`        | Veterano           | Reduz custo de Golpe Poderoso em 1 raiva/pt             |
| `cav_maquina_matar`   | Máquina de Matar   | Impacto +15% dmg/inimigo no raio (behavioral)           |
| `cav_assassino`       | Assassino          | +5% chance/pt de gerar carga livre de Executar ao acertar com Impacto |
| `cav_horrorizante`    | Horrorizante       | Se Executar não matar, alvo foge por 1s (behavioral)    |
| `cav_punho_queixo`    | Punho no Queixo    | Desbloqueia skill; stun curto                           |
| `cav_fatiador_corpos` | Fatiador de Corpos | Desbloqueia skill de channeling                         |

---

## 10. Sistema de Som

**Arquivo:** `sound_manager.py`
**Singleton global:** `SOUNDS` (importado por `game.py`, `systems.py` e `stats_system.py`)

### Formato de arquivo

**OGG Vorbis (`.ogg`)** — suportado nativamente pelo Pygame, boa compressão, loop sem gap.

### Estrutura de pastas

```
assets/sounds/
├── sfx/
│   ├── skill_*.ogg               ← sons de habilidades
│   ├── hit_normal*.ogg           ← auto-ataque normal (variações)
│   ├── hit_crit*.ogg             ← auto-ataque crítico (variações)
│   ├── step_1.ogg … step_4.ogg  ← passos do player
│   ├── combat_miss_*.ogg         ← ataque errou
│   ├── combat_parry_*.ogg        ← alvo aparou
│   ├── combat_dodge_*.ogg        ← alvo desviou
│   ├── combat_block_*.ogg        ← alvo bloqueou
│   ├── mob_aggro.ogg             ← fallback genérico
│   ├── mob_death.ogg
│   ├── mob_crit.ogg
│   ├── mob_attack_melee.ogg      ← fallback ataque melee
│   ├── mob_attack_ranged.ogg     ← fallback ataque ranged (Hunter)
│   ├── mob_attack_magic.ogg      ← fallback ataque mágico (Mage)
│   ├── mob_{race}_*.ogg          ← sons por raça (aggro/death/crit/attack_*)
│   ├── loot_gold.ogg
│   ├── loot_item.ogg
│   ├── inventory_open/close.ogg
│   ├── talent_open/close.ogg
│   ├── map_open/close.ogg
│   └── levelup.ogg
│
├── sfx/cave/                     ← versões pré-processadas com reverb
│   └── (mesmos nomes de sfx/)    ← gerados pelo reverb.py
│
└── ambient/
    ├── map_surface.ogg
    └── map_cave.ogg
```

### API pública

```python
SOUNDS.play("nome")                              # SFX pelo nome exato
SOUNDS.play_random(["s1","s2","s3"])             # Sorteia um da lista (ignora ausentes)
SOUNDS.play_skill("skill_executar")              # Sorteia entre até 4 variações
SOUNDS.play_footstep()                           # Passo do player (15% silêncio)
SOUNDS.play_mob_event("Lobo", "attack_melee")    # Som de mob com fallback em cadeia
SOUNDS.play_ui("inventory_open")                 # Canais de UI
SOUNDS.play_mob("mob_death")                     # Canais de mob (uso direto)
SOUNDS.play_ambient("ambient_cave")              # Loop de ambiente (fade in/out)
SOUNDS.stop_ambient()                            # Para o ambiente com fadeout
SOUNDS.set_context("cave")                       # Troca para sons de caverna
SOUNDS.set_context("surface")                    # Volta para sons normais
SOUNDS.update(dt)                                # Chamado a cada frame
```

### Contexto de caverna (arquivos pré-processados)

Quando `set_context("cave")` está ativo, `play()` busca automaticamente a versão
da pasta `sfx/cave/` antes de usar o arquivo normal:

- **Cave disponível** → toca `sfx/cave/{nome}.ogg` (com reverb)
- **Cave ausente** → fallback para `sfx/{nome}.ogg`
- Gerado pelo script `reverb.py` com `pedalboard` (Reverb: room=0.75, wet=0.4, dry=0.6)

### Variações de skills (até 4)

`play_skill(name)` tenta automaticamente `name`, `name_2`, `name_3`, `name_4`
e sorteia entre os disponíveis. Arquivos extras são opcionais:

```
skill_golpe_poderoso.ogg      ← obrigatório (base)
skill_golpe_poderoso_2.ogg    ← opcional
skill_golpe_poderoso_3.ogg    ← opcional
skill_golpe_poderoso_4.ogg    ← opcional
```

O `sound_name` de cada skill é definido automaticamente ao criá-la (`skill_{skill_id}`).
Para sobrescrever: `skill.sound_name = "outro_nome"`.

### Passos do player

- **Arquivo:** `TileMovementSystem` dispara ao completar cada tile
- **Intervalo mínimo:** 0.20s entre disparos
- **Silêncio:** 15% de chance por passo
- **Dash:** sem som de passo durante Interceptar

### Sons de combate reativo

Disparados em `CombatSystem.deal_damage()` conforme o outcome:

| Outcome  | Evento          | Arquivos (até 4 variações)          |
|----------|-----------------|-------------------------------------|
| `miss`   | Ataque errou    | `combat_miss_1.ogg` … `_4.ogg`     |
| `parry`  | Alvo aparou     | `combat_parry_1.ogg` … `_4.ogg`    |
| `dodge`  | Alvo desviou    | `combat_dodge_1.ogg` … `_4.ogg`    |
| `block`  | Alvo bloqueou   | `combat_block_1.ogg` … `_4.ogg`    |

### Sons de ataque de mobs

`play_mob_event(race, event)` — fallback em cadeia: `mob_{race}_{event}` → `mob_{event}` → silêncio.

**Eventos de ataque** determinados pela IA:

| Condição                          | Evento           | Genérico                |
|-----------------------------------|------------------|-------------------------|
| Melee físico                      | `attack_melee`   | `mob_attack_melee.ogg`  |
| Ranged físico (Hunter)            | `attack_ranged`  | `mob_attack_ranged.ogg` |
| Mágico (Mage, ranged ou melee)    | `attack_magic`   | `mob_attack_magic.ogg`  |

**Outros eventos:**

| Evento   | Condição                                   |
|----------|--------------------------------------------|
| `aggro`  | Mob avista player (`IDLE → CHASING`)       |
| `death`  | Mob morre (`_handle_death`)                |
| `crit`   | Mob recebe golpe crítico do player         |

### Sons de skills — disparo centralizado

O som é tocado em `_use_skill()` **após** o handler retornar `True`.
Handlers nunca chamam `SOUNDS.play_skill()` diretamente.
`deal_damage()` com `is_ability=True` suprime `hit_normal`/`hit_crit` — skills têm som próprio.

### Sons de modais — fechamento centralizado

O som de fechamento é emitido em dois pontos:
- **`_close_all_modals()`** — detecta o modal aberto e emite o som (cobre troca de modais)
- **ESC handler** — emite o som ao fechar o modal ativo individualmente

### Canais Pygame (16 no total)

| Canais | Uso                        |
|--------|----------------------------|
| 0      | Ambiente (loop exclusivo)  |
| 1–3    | Skills do player           |
| 4–5    | Sons de UI e loot          |
| 6–9    | Sons de mobs               |
| 10–15  | Hits, passos e SFX gerais  |

### Como adicionar um novo som

1. Coloque `.ogg` em `assets/sounds/sfx/`
2. Adicione entrada em `_REGISTRY` (`sound_manager.py`): `"meu_som": _sfx("meu_som")`
3. Opcionalmente gere versão cave com `reverb.py`
4. Chame `SOUNDS.play("meu_som")` onde necessário

### Como adicionar sons para um novo mob

1. Crie os arquivos: `mob_{race}_aggro.ogg`, `mob_{race}_death.ogg`, `mob_{race}_crit.ogg`,
   `mob_{race}_attack_melee.ogg`, `mob_{race}_attack_ranged.ogg`, `mob_{race}_attack_magic.ogg`
2. Adicione as entradas no `_REGISTRY`
3. Os sistemas já chamam `play_mob_event(identity.race, event)` automaticamente

### Onde cada som é disparado

| Som                              | Arquivo            | Condição                                             |
|----------------------------------|--------------------|------------------------------------------------------|
| `skill_*`                        | `systems.py`       | `_use_skill()` após handler retornar True            |
| `hit_normal_*` / `hit_crit_*`    | `systems.py`       | `deal_damage()`, attacker=player, `is_ability=False` |
| `step_*`                         | `systems.py`       | `TileMovementSystem`, tile completo, 85% chance      |
| `combat_miss/parry/dodge/block`  | `systems.py`       | `deal_damage()`, conforme outcome                    |
| `mob_{race}_aggro`               | `systems.py`       | `EnemyAISystem`: `IDLE → CHASING`                    |
| `mob_{race}_attack_*`            | `systems.py`       | `EnemyAISystem`: ataque melee ou ranged com LOS      |
| `mob_{race}_crit`                | `systems.py`       | `deal_damage()`, crítico em mob                      |
| `mob_{race}_death`               | `systems.py`       | `_handle_death()`, entidade não-player               |
| `loot_gold` / `loot_item`        | `systems.py`       | `LootSystem._try_take_item()`                        |
| `inventory_open`                 | `game.py`          | Tecla I (abrir)                                      |
| `inventory_close`                | `game.py`          | Tecla I (fechar), ESC, ou troca de modal             |
| `talent_open/close`              | `game.py`          | Tecla T / ESC / troca de modal                       |
| `map_open/close`                 | `game.py`          | Tecla M / ESC / troca de modal                       |
| `levelup`                        | `stats_system.py`  | `XPSystem`: ao subir de nível                        |
| `ambient_*`                      | `game.py`          | `_load_map_and_entities()` / `_do_transition()`      |

---

### Tabela de referência — todos os arquivos OGG

#### Interface e menus (`assets/sounds/sfx/`)

| Ação                         | Arquivo OGG            |
|------------------------------|------------------------|
| Abrir inventário             | `inventory_open.ogg`   |
| Fechar inventário            | `inventory_close.ogg`  |
| Abrir árvore de talentos     | `talent_open.ogg`      |
| Fechar árvore de talentos    | `talent_close.ogg`     |
| Abrir mapa                   | `map_open.ogg`         |
| Fechar mapa                  | `map_close.ogg`        |
| Subir de nível               | `levelup.ogg`          |

#### Loot (`assets/sounds/sfx/`)

| Ação            | Arquivo OGG      |
|-----------------|------------------|
| Coletar moedas  | `loot_gold.ogg`  |
| Coletar item    | `loot_item.ogg`  |

#### Passos do player (`assets/sounds/sfx/`)

| Variação | Arquivo OGG  |
|----------|--------------|
| 1        | `step_1.ogg` |
| 2        | `step_2.ogg` |
| 3        | `step_3.ogg` |
| 4        | `step_4.ogg` |

#### Habilidades do player (`assets/sounds/sfx/`)

Cada skill suporta até 4 variações: `skill_X.ogg`, `skill_X_2.ogg`, `skill_X_3.ogg`, `skill_X_4.ogg`.
Apenas a variação base é obrigatória; as extras são opcionais.

| Skill               | Arquivo base OGG                  |
|---------------------|-----------------------------------|
| Interceptar         | `skill_interceptar.ogg`           |
| Executar            | `skill_executar.ogg`              |
| Impacto             | `skill_impacto.ogg`               |
| Golpe Poderoso      | `skill_golpe_poderoso.ogg`        |
| Golpe Debilitante   | `skill_golpe_debilitante.ogg`     |
| Fatiador de Corpos  | `skill_fatiador.ogg`              |
| Punho no Queixo     | `skill_punho_queixo.ogg`          |
| Vitória Iminente    | `skill_vitoria_iminente.ogg`      |
| Provocar            | `skill_provocar.ogg`              |
| Redoma              | `skill_redoma.ogg`                |
| Baluarte            | `skill_baluarte.ogg`              |
| Sangramento         | `skill_sangramento.ogg`           |
| Duelo               | `skill_duelo.ogg`                 |
| Investida           | `skill_investida.ogg`             |
| Paralisação         | `skill_paralisacao.ogg`           |
| Carga Heroica       | `skill_carga_heroica.ogg`         |

#### Auto-ataque do player (`assets/sounds/sfx/`)

Apenas durante auto-ataques (`is_ability=False`). Skills suprimem estes sons.

| Ação                       | Arquivo OGG        |
|----------------------------|--------------------|
| Acerto normal — variação 1 | `hit_normal_1.ogg` |
| Acerto normal — variação 2 | `hit_normal_2.ogg` |
| Acerto normal — variação 3 | `hit_normal_3.ogg` |
| Acerto normal — fallback   | `hit_normal.ogg`   |
| Crítico — variação 1       | `hit_crit_1.ogg`   |
| Crítico — variação 2       | `hit_crit_2.ogg`   |
| Crítico — fallback         | `hit_crit.ogg`     |

#### Combate reativo (`assets/sounds/sfx/`)

Até 4 variações por evento (sufixo `_1` a `_4`):

| Evento          | Arquivos                                    |
|-----------------|---------------------------------------------|
| Ataque errou    | `combat_miss_1.ogg` … `combat_miss_4.ogg`  |
| Alvo aparou     | `combat_parry_1.ogg` … `combat_parry_4.ogg`|
| Alvo desviou    | `combat_dodge_1.ogg` … `combat_dodge_4.ogg`|
| Alvo bloqueou   | `combat_block_1.ogg` … `combat_block_4.ogg`|

#### Sons de mobs — genéricos / fallback (`assets/sounds/sfx/`)

| Evento              | Arquivo OGG              |
|---------------------|--------------------------|
| Aggro               | `mob_aggro.ogg`          |
| Morte               | `mob_death.ogg`          |
| Recebeu crítico     | `mob_crit.ogg`           |
| Ataque melee        | `mob_attack_melee.ogg`   |
| Ataque ranged       | `mob_attack_ranged.ogg`  |
| Ataque mágico       | `mob_attack_magic.ogg`   |

#### Sons de mobs — por raça (`assets/sounds/sfx/`)

Convenção: `mob_{race}_attack_melee.ogg`, `mob_{race}_attack_ranged.ogg`, `mob_{race}_attack_magic.ogg`.

| Mob       | Aggro                     | Morte                     | Crítico                   | Ataque (base do nome)          |
|-----------|---------------------------|---------------------------|---------------------------|--------------------------------|
| Aranha    | `mob_aranha_aggro.ogg`    | `mob_aranha_death.ogg`    | `mob_aranha_crit.ogg`     | `mob_aranha_attack_*.ogg`      |
| Rato      | `mob_rato_aggro.ogg`      | `mob_rato_death.ogg`      | `mob_rato_crit.ogg`       | `mob_rato_attack_*.ogg`        |
| Escorpião | `mob_escorpiao_aggro.ogg` | `mob_escorpiao_death.ogg` | `mob_escorpiao_crit.ogg`  | `mob_escorpiao_attack_*.ogg`   |
| Cobra     | `mob_cobra_aggro.ogg`     | `mob_cobra_death.ogg`     | `mob_cobra_crit.ogg`      | `mob_cobra_attack_*.ogg`       |
| Lobo      | `mob_lobo_aggro.ogg`      | `mob_lobo_death.ogg`      | `mob_lobo_crit.ogg`       | `mob_lobo_attack_*.ogg`        |
| Urso      | `mob_urso_aggro.ogg`      | `mob_urso_death.ogg`      | `mob_urso_crit.ogg`       | `mob_urso_attack_*.ogg`        |
| Goblin    | `mob_goblin_aggro.ogg`    | `mob_goblin_death.ogg`    | `mob_goblin_crit.ogg`     | `mob_goblin_attack_*.ogg`      |
| Zumbi     | `mob_zumbi_aggro.ogg`     | `mob_zumbi_death.ogg`     | `mob_zumbi_crit.ogg`      | `mob_zumbi_attack_*.ogg`       |
| Orc       | `mob_orc_aggro.ogg`       | `mob_orc_death.ogg`       | `mob_orc_crit.ogg`        | `mob_orc_attack_*.ogg`         |
| Troll     | `mob_troll_aggro.ogg`     | `mob_troll_death.ogg`     | `mob_troll_crit.ogg`      | `mob_troll_attack_*.ogg`       |
| Elfo      | `mob_elfo_aggro.ogg`      | `mob_elfo_death.ogg`      | `mob_elfo_crit.ogg`       | `mob_elfo_attack_*.ogg`        |
| Minotauro | `mob_minotauro_aggro.ogg` | `mob_minotauro_death.ogg` | `mob_minotauro_crit.ogg`  | `mob_minotauro_attack_*.ogg`   |
| Vampiro   | `mob_vampiro_aggro.ogg`   | `mob_vampiro_death.ogg`   | `mob_vampiro_crit.ogg`    | `mob_vampiro_attack_*.ogg`     |
| Dragão    | `mob_dragao_aggro.ogg`    | `mob_dragao_death.ogg`    | `mob_dragao_crit.ogg`     | `mob_dragao_attack_*.ogg`      |

> `*` = `melee`, `ranged` ou `magic`

#### Ambiente (`assets/sounds/ambient/`)

| Contexto    | Arquivo OGG       |
|-------------|-------------------|
| Superfície  | `map_surface.ogg` |
| Caverna     | `map_cave.ogg`    |

---

## 11. Tabelas de Loot

*(sem alterações em relação à versão anterior)*

---

## 12. Constantes e Configurações

| Constante                            | Valor  | Arquivo            |
|--------------------------------------|--------|--------------------|
| `TILE_SIZE`                          | 32     | tileset.py         |
| `SCREEN_WIDTH`                       | 1280   | game.py            |
| `SCREEN_HEIGHT`                      | 720    | game.py            |
| `FPS`                                | 60     | game.py            |
| `CRITICAL_DAMAGE_MULTIPLIER`         | 2.0    | systems.py         |
| `CombatState.OUT_OF_COMBAT_DURATION` | 6.0s   | components.py      |
| `Corpse.DECAY_TIME`                  | 120.0s | components.py      |
| `Corpse.LOOTED_DECAY_TIME`           | 15.0s  | components.py      |
| `CameraSystem.LERP_SPEED`            | 8.0    | systems.py         |
| `SkillSystem.INTERCEPT_MIN_RANGE`    | 2 tiles| systems.py         |
| `SkillSystem.INTERCEPT_MAX_RANGE`    | 6 tiles| systems.py         |
| `SkillSystem.INTERCEPT_DURATION`     | 0.18s  | systems.py         |
| `SkillSystem.AoE_RADIUS`             | 3 tiles| systems.py         |
| `DashTrailManager.DURATION`          | 0.25s  | floating_text.py   |
| `SoundManager.ECHO_DELAY`            | 0.13s  | sound_manager.py   |
| `SoundManager.ECHO_VOLUME`           | 0.30   | sound_manager.py   |
| `Inventory.max_slots`                | 20     | components.py      |
| `LootSystem.MAX_ROWS`                | 5      | systems.py         |
| `CombatLog.MAX_MESSAGES`             | 8      | combat_log.py      |

---

## 13. Guia de Configuração de Assets

### 13.1 Ícones de Habilidades (Hotbar)

**Pasta:** `assets/icons/`

Cada skill usa o campo `icon_name = "skill_<skill_id>"`:

```
assets/icons/skill_golpe_poderoso.png
assets/icons/skill_cura.png
assets/icons/skill_impacto.png
assets/icons/skill_executar.png
assets/icons/skill_interceptar.png
assets/icons/skill_golpe_debilitante.png
assets/icons/skill_punho_queixo.png
assets/icons/skill_fatiador_corpos.png
```

### 13.2 Sons

**Pasta:** `assets/sounds/`
Ver [seção 10](#10-sistema-de-som) para a estrutura completa.

### 13.3 Ícones de Itens

```python
# Convenção: item_key(item) → f"item_{item.name.lower().replace(' ', '_')}"
"Espada de Osso" → assets/icons/item_espada_de_osso.png
```

### 13.4 Sprites (futuro)

```
assets/sprites/player.png
assets/sprites/enemy_normal_melee.png
assets/sprites/enemy_elite_ranged.png
assets/sprites/corpse.png
```

### 13.5 Tabela de Referência Rápida

| Objetivo                              | Arquivo principal              | O que editar                                    |
|---------------------------------------|--------------------------------|-------------------------------------------------|
| Adicionar novo som                    | `sound_manager.py`             | Entrada em `_REGISTRY` + arquivo `.ogg`         |
| Adicionar nova skill                  | `components.py`, `systems.py`  | Entrada em `SKILL_SLOTS` + `_skill_*()`         |
| Adicionar novo talento                | `talent_data.py`               | Entrada em `TALENTS`                            |
| Adicionar efeito de talento behavioral| `systems.py`                   | Checar `_tt.allocated.get("id", 0)` no sistema  |
| Mudar contexto acústico do mapa       | `game.py`                      | Condição `"cave" in target_file`                |
| Adicionar novo mapa                   | `maps/`, `game.py`             | Novo CSV + JSON de entidades + `MAP_FILES`      |
| Adicionar item                        | `loot_tables.py`               | Novo lambda em `_T`, adicionar à tabela         |
| Mudar stats base do jogador           | `entity_factory.py`            | Parâmetros de `CombatStats` em `create_player()`|
| Mudar stats base do inimigo           | `entity_factory.py`            | `ENEMY_TIER_CONFIGS`                            |
| Mudar fórmula de XP                   | `components.py`                | `CharacterStats.xp_for_level()`                 |
| Mudar fórmula de progressão           | `stats_system.py`              | `apply_char_stats_to_combat()`                  |
| Mudar escala de resolução             | `settings_screen.py`           | Lista de opções + `config.json`                 |

---

## 14. Avaliação Arquitetural e Débito Técnico

> Avaliação realizada em 2026-03-22. Objetivo: identificar desvios do padrão ECS, riscos de escalabilidade e orientar decisões futuras de refatoração.

### 14.1 Nota Geral: 6/10

O projeto tem uma base sólida — `world.py` é simples e correto, os componentes menores são dados puros e o padrão de fábrica está bem aplicado. O crescimento orgânico do projeto introduziu acoplamentos que, se não monitorados, vão dificultar a adição de novas features.

---

### 14.2 O que está bem feito

| Área | Arquivo | Por que funciona |
|------|---------|-----------------|
| Registro ECS | `world.py` | Simples, sem lógica, API mínima e coesa |
| Componentes de posição/movimento | `components.py` — `Position`, `TileMovement`, `Renderable` | Dados puros sem efeito colateral |
| Base dos sistemas | `systems.py:21-32` | `update()` / `render()` separados, sem dependências ocultas |
| TileMovementSystem | `systems.py:1665-1709` | Responsabilidade única, ~44 linhas |
| CameraSystem | `systems.py:1754-1769` | LERP elegante, ~15 linhas |
| PathfindingSystem | `systems.py:35-133` | A* correto com limite de nós |
| Factory pattern | `entity_factory.py` | Criação de entidades centralizada |
| Map loader | `map_loader.py` | Suporta CSV + JSON override, validação integrada |
| Fórmula de progressão | `stats_system.py` — `apply_char_stats_to_combat()` | Clara e bem documentada |
| Skill sound_name dinâmico | `components.py` — `PlayerSkills.__init__` | Nova skill ganha som automaticamente |

---

### 14.3 Problemas Críticos

#### C1 — Lógica de negócio em componentes
**Arquivo:** `components.py` — `CombatStats._recalculate_effective_stats()` (linhas ~135-160), `CombatState.update()` (linhas ~365-377)

Componentes devem ser dados puros. Cálculo de stats efetivos e atualização de timers pertencem a sistemas. O efeito prático é que qualquer mudança em balanceamento de combate exige entender o fluxo de recalculação dentro do componente — que é invisível ao sistema.

**Regra a seguir:** componentes podem ter propriedades `@property` somente-leitura que derivam valores de campos base. Nunca métodos que modificam estado.

---

#### C2 — God Systems
Três sistemas acumularam responsabilidades demais:

**`CombatSystem` (~350 linhas):** calcula dano, aplica redução de armadura, gera textos flutuantes, emite sons, cria cadáveres, rola loot, gerencia morte. Cada uma dessas responsabilidades deveria ser encapsulada separadamente.

**`EnemyAISystem` (~545 linhas):** detecta sono, resolve stun/fear, calcula linha de visão, faz pathfinding, decide kiting/pursuing/idle, spawna projéteis, emite sons por raça, executa ataques. Qualquer mudança em IA toca código de combate e vice-versa.

**`GameEngine.run()` (~230 linhas):** mistura game loop, handling de eventos, controle de modais, transições de mapa e chamadas de render. Adicionar um novo modal requer entender todo esse bloco.

---

#### C3 — Chamadas diretas entre sistemas (acoplamento não-ECS)
O padrão ECS puro usa o World como intermediário. O projeto faz chamadas diretas:

| Chamador | Chamado | Localização |
|----------|---------|-------------|
| `EnemyAISystem` | `CombatSystem.deal_damage()` | `systems.py:1326` |
| `EnemyAISystem` | `PathfindingSystem.find_path()` | `systems.py:1451` |
| `PlayerInputSystem` | `CombatSystem.deal_damage()` | `systems.py:935, 1074` |
| `CombatSystem` | `create_corpse()` (entity factory) | `systems.py:470` |
| `GameEngine` | `apply_char_stats_to_combat()` | `game.py:598` |

O risco: trocar qualquer um dos sistemas chamados exige alterar todos os chamadores.

---

### 14.4 Problemas Moderados

#### M1 — Código duplicado em movimento de tiles
A lógica de iniciar um movimento tile-a-tile está copiada em três lugares:
- `PlayerInputSystem._start_tile_movement()` — `systems.py:~1032`
- `EnemyAISystem.update()` (inline) — `systems.py:~1470`
- `EnemyAISystem._do_kiting()` — `systems.py:~1518`

Um bug corrigido em um lugar precisa ser corrigido nas outras duas cópias.

#### M2 — Cálculo de distância Chebyshev inline
Repetido 3+ vezes dentro de `EnemyAISystem.update()`. Candidato a função auxiliar `chebyshev(a, b)` em um módulo utilitário.

#### M3 — GameEngine manipula componentes diretamente
`_debug_levelup()`, `_reposition_player()` e `_draw_hotbar()` leem e escrevem componentes sem passar por sistemas. Qualquer novo componente adicionado ao player requer mudanças nessas funções.

#### M4 — Ordem dos sistemas é frágil
Os 18+ sistemas em `self.systems` dependem de uma ordem específica sem documentação de dependências. Inserir um novo sistema na posição errada pode causar bugs silenciosos.

#### M5 — Pathfinding sem budget por frame
`EnemyAISystem` não limita quantos inimigos podem fazer A* no mesmo frame. Com 50+ inimigos ativos, isso pode causar travamentos.

---

### 14.5 Problemas Leves

#### L1 — Singletons globais (`SOUNDS`, `FLT`, `LOG`)
Qualquer sistema que queira emitir som, texto flutuante ou log precisa importar o singleton. Dificulta testes unitários e substituição futura por outro backend de áudio/UI.

#### L2 — `CombatStats` cresce linearmente
Cada novo atributo de combate exige: campo base, campo efetivo, linha em `_recalculate_effective_stats()` e linha em `apply_char_stats_to_combat()`. Com 10+ novos atributos planejados, esse arquivo vai crescer muito.

#### L3 — Sem validação de composição de entidade
Nada impede adicionar `AIControlled` ao player ou `PlayerControlled` a um inimigo. Erros de configuração na factory só aparecem em runtime.

---

### 14.6 Decisões de Arquitetura para Features Futuras

Antes de implementar cada feature abaixo, considere:

**Múltiplos mapas simultâneos / dungeons instanciadas**
- Problema atual: `GameEngine` assume um único tilemap ativo
- O que fazer antes: extrair o contexto de mapa (tilemap + entidades) em um objeto separado que pode ser trocado atomicamente

**Mais de 30 inimigos ativos no mesmo frame**
- Problema atual: A* sem budget por frame
- O que fazer antes: adicionar um sistema de fila de pathfinding com limite de N cálculos por frame

**Novos tipos de dano / status effects**
- Problema atual: lógica de stun/fear hardcoded em `EnemyAISystem`, não em `StatusEffectSystem`
- O que fazer antes: centralizar todos os status effects em um sistema dedicado com tabela de efeitos

**Novo modal de UI (crafting, guild, etc.)**
- Problema atual: `GameEngine.run()` gerencia todos os modais inline
- O que fazer antes: criar um `ModalManager` que cada sistema pode registrar/deregistrar

**Sistema de replay ou save/load**
- Problema atual: estado espalhado entre componentes e sistemas (flags em `AIControlled`, timers em `CombatState`)
- O que fazer antes: garantir que todo estado de jogo relevante está exclusivamente em componentes (não em atributos de instância de sistemas)

---

### 14.7 Princípios a Seguir nos Próximos Desenvolvimentos

1. **Componentes são dados, sistemas são lógica.** Se um componente precisa de um método que modifica outro componente, mova para um sistema.

2. **Sistemas não chamam sistemas.** Use componentes como sinal: o sistema A escreve um componente de "request" (`DamageRequest`, `MoveRequest`), o sistema B lê e executa.

3. **Cada sistema faz uma coisa.** Se o nome de um sistema usa "e" (ex: "resolve colisão *e* aplica dano"), é candidato a divisão.

4. **Documente a ordem dos sistemas.** Cada entrada em `self.systems` deve ter um comentário com sua dependência: "precisa rodar antes de X porque Y".

5. **Novas features não alteram `GameEngine.run()`.** Se uma feature nova exige mudança no game loop principal, considere se ela deveria ser um sistema independente.
