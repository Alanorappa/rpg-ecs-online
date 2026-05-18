# Sistemas ECS — Referência Completa

> Todos os sistemas do jogo, em ordem de execução, com responsabilidades e dependências.

---

## Ordem de execução — `game.py self.systems`

| Pos | Sistema | Arquivo | Responsabilidade | Eventos |
|-----|---------|---------|------------------|---------|
| 1 | **TileValidationSystem** | systems.py | Reconstrói cache de tiles ocupados; valida walkability | Não |
| 2 | **AoeTargetingSystem** | spell_system.py | Mira de spells AOE (Calamidade); clique esq. dispara | **Sim** |
| 3 | **MouseTargetingSystem** | systems.py | Clique dir. = perseguir inimigo; clique esq. = selecionar/deselecionar | **Sim** |
| 4 | **LootSystem** | systems.py | Abre modal de loot; coleta itens de cadáveres | **Sim** |
| 5 | **PlayerInputSystem** | systems.py | Input de teclado/mouse: movimento, auto-ataque, auto-move | **Sim** |
| 6 | **SkillSystem + SkillHandlers** | systems.py + skill_handlers.py | Dispatch de skills por `skill_id`; GCD | **Sim** |
| 7 | **EnemyAISystem** | systems.py | Pathfinding, estados (IDLE/CHASING/ATTACKING), kiting, leash | Não |
| 8 | **EnemyAbilitySystem** | systems.py | Cooldowns e triggers de habilidades especiais de mobs | Não |
| 9 | **ProjectileSystem** | systems.py | Move projéteis de inimigos; aplica dano ao acertar | Não |
| 10 | **PlayerProjectileSystem** | spell_system.py | Move projéteis do mago; resolve crit, burn, procs | Não |
| 11 | **SpellCastSystem** | spell_system.py | Processa barra de cast; completa ou cancela | Não |
| 12 | **ChannelingSystem** | spell_system.py | Ticks de canalização (Calamidade Flamejante); mana/s | **Sim** |
| 13 | **IceBlockSystem** | spell_system.py | Duração do Bloco de Gelo; regen HP por tick | Não |
| 14 | **FireShieldSystem** | spell_system.py | Duração do Escudo de Fogo; remove ao expirar | Não |
| 15 | **PirofagiaSystem** | spell_system.py | Mira de cone em tempo real; dispara no clique | **Sim** |
| 16 | **ManaSystem** | spell_system.py | Regen de mana; decrementa fire_crit_timer; thermal_shock_active | Não |
| 17 | **DeathHandlerSystem** | systems.py | Processa PendingDeath: loot, corpse, XP, respawn de zona | Não |
| 18 | **CorpseSystem** | systems.py | Decay de cadáveres (timer); remove entidade ao expirar | Não |
| 19 | **SpawnZoneSystem** | systems.py | Gerencia contagem de spawns ativos por zona | Não |
| 20 | **XPSystem** | stats_system.py | Consome `pending_xp`, aplica level-up | Não |
| 21 | **DeathRespawnSystem** | stats_system.py | Respawn do jogador; restaura HP | Não |
| 22 | **ConsumableSystem** | systems.py | Processa ActiveRegen (efeito de consumíveis) | Não |
| 23 | **CombatStateSystem** | systems.py | Timers de combate (stun, in_combat); procs ao entrar em combate | Não |
| 24 | **StatusEffectSystem** | systems.py | Decrementar duração; aplicar ticks (poison, burn, regen, elemental_lapse…); morte por DoT | Não |
| 25 | **TileMovementSystem** | systems.py | Interpolação de movimento; atualiza elevation; som de passos | Não |
| 26 | **FogSystem** | systems.py | Shadowcasting (8 octantes); atualiza Visible tags | Não |
| 27 | **CameraSystem** | systems.py | Suaviza câmera em direção ao player | Não |
| 28 | **TileRenderSystem** | systems.py | Cache de tiles (terrain + objects); render com ysort | Não |
| 29 | **RenderSystem** | systems.py | Ysort de entidades + objetos; HP bars; highlight de alvo | Não |

### Sistemas fora da lista principal (chamados explicitamente em `game.py`)

| Sistema | Quando chamado | Arquivo |
|---------|----------------|---------|
| **QuestSystem** | inserido em `self.systems` via `insert()` após XPSystem | quest_system.py |
| **ShopSystem** | `self._shop_system.update()` + `render_world()` separados | systems.py |
| **BlacksmithSystem** | `self._crafting_system.update()` + `handle_events()` | crafting_system.py |
| **TrainerSystem** | `self._trainer_system.update()` + `handle_events()` | trainer_system.py |
| **QuestDialogSystem** | `render_world()` separado | quest_system.py |
| **TalentSystem** | `render()` via `_show_talents` flag | talent_system.py |
| `_aoe_targeting_system.render()` | **após** fog + entities | spell_system.py |
| `_pirofagia_system.render()` | **após** aoe_targeting | spell_system.py |
| `_projectile_system.render()` | **após** fog | systems.py |
| `_player_proj_system.render()` | **após** fog | spell_system.py |
| `_channeling_system.render()` | **após** fog | spell_system.py |

> **Regra:** sistemas que rendem por cima de tiles/entidades precisam ser chamados explicitamente APÓS `_tile_render_system.render_fog()`, não dentro do loop genérico.

---

## Sistemas do Servidor (branch online) — `server/`

> Estes sistemas rodam **sem Pygame**, em asyncio, no processo do servidor.
> Nunca importam `pygame`, `game.py` ou qualquer código de render.

### Loop de ticks — `server/world_server.py`

O servidor roda a **20 ticks/s** (50ms por tick). A cada tick:
1. Executa `system.update(dt)` para cada sistema do servidor
2. Chama `_collect_deltas()` — coleta o que mudou
3. Chama `_store_snapshot()` — guarda posições para lag compensation
4. Chama callbacks `_on_tick_callbacks` → `SessionManager._on_tick()`

### Sistemas do servidor (a implementar progressivamente)

| Ordem | Sistema | Responsabilidade | Status |
|-------|---------|------------------|--------|
| 1 | **MovementSystem** (servidor) | Valida e aplica tile-movement de jogadores; anti-teleporte | 🔲 pendente |
| 2 | **EnemyAISystem** (servidor) | Pathfinding, aggro, leash — idêntico ao offline mas sem render | 🔲 pendente |
| 3 | **CombatSystem** (servidor) | Auto-attack loop, resolução hit/miss/crit via `damage_calculator.py` | 🔲 pendente |
| 4 | **SkillSystem** (servidor) | Valida CD, recursos; executa efeitos; lag compensation p/ cones | 🔲 pendente |
| 5 | **StatusEffectSystem** (servidor) | Ticks de DoT, duração de buffs/debuffs | 🔲 pendente |
| 6 | **ProjectileSystem** (servidor) | Física de projéteis; confirma hit ou miss; envia PROJECTILE_HIT | 🔲 pendente |
| 7 | **SpawnSystem** (servidor) | Respawn de mobs por zona; contagem de ativos | 🔲 pendente |
| 8 | **DeathSystem** (servidor) | Processa mortes: loot, XP, respawn | 🔲 pendente |
| 9 | **AOISystem** (servidor) | Calcula quais entidades entraram/saíram do FOV de cada jogador | 🔲 pendente |

### Regra de separação cliente/servidor

| Responsabilidade | Onde roda | Justificativa |
|-----------------|-----------|---------------|
| Cálculo de dano | Servidor | Anti-cheat |
| Posição de entidades | Servidor (canônico) | Anti-teleporte |
| IA de mobs | Servidor | Consistência entre clientes |
| Animações, partículas | Cliente | Cosmético, não afeta gameplay |
| Previsão de movimento | Cliente | Client-side prediction para fluidez |
| Aiming de cone (Pirofagia, Tiro Múltiplo) | Cliente envia direção, servidor valida | Lag compensation |
| Invisibilidade (Camuflagem) | Servidor não envia posição a outros | Segurança — cliente nunca recebe dado de invisível |

### Protocolo de adição de sistema no servidor

1. Criar classe em `server/` herdando de `System` do `world.py` (ou classe simples com `update(dt)`)
2. Instanciar em `WorldServer._init_systems()` e adicionar a `self._systems`
3. Se gerar deltas para clientes → adicionar campo em `WorldServer._collect_deltas()`
4. Se precisar de lag compensation → usar `WorldServer.get_snapshot_at(tick)`
5. Documentar na tabela acima com status ✅

---

> ⚠️ **Problema de qualidade:** ShopSystem, LootSystem e CraftingSystem misturam UI e lógica de negócio. Ver `PROBLEMAS_ARQUITETURA.md` problema #9.

## Serviços de sistema (módulo-nível)

Funções registradas via `register_services()` em `game.py`. Qualquer sistema pode chamar sem referência direta:

```python
from systems import deal_damage, find_path, is_tile_walkable, get_tilemap, get_mainhand_weapon
```

| Função | Implementação real | Para quê |
|--------|-------------------|----------|
| `deal_damage(attacker, target, type, ...)` | `CombatSystem.deal_damage` | Aplica dano + feedback |
| `find_path(start, end, obstacles, ...)` | `PathfindingSystem.find_path` | A* com bounded search |
| `is_tile_walkable(entity, tx, ty, ...)` | `TileValidationSystem.is_tile_walkable` | Valida caminhabilidade |
| `get_tilemap()` | `PathfindingSystem._get_tilemap_component` | Acessa Tilemap component |
| `get_mainhand_weapon(world, entity)` | helper | Retorna item da mainhand |
| `apply_effect(world, eid, type, dur, mag)` | module-level em systems.py | Aplica/refresha status effect |
| `sync_attack_interval(cs, equip)` | stats_system.py | Sincroniza velocidade de ataque com arma equipada — só na criação/load, NUNCA no level-up |
| `fadeout_skills(ms)` | sound_manager.py `SOUNDS` | Fade out de canais de skill ao cancelar cast/canalização |

---

## Classe base `System`

```python
class System:
    world_surf: pygame.Surface   # surface de mundo (zoom_surf) — atribuída por _assign_world_surf()
    hud_surf:   pygame.Surface   # surface da tela nativa — NÃO é atualizada por _assign_world_surf

    def update(self, events, dt): ...
    def render(self, cam_x=0, cam_y=0): ...
```

> `world_surf` é substituída todo frame pelo `GameEngine` para o `_zoom_surf` (superfície com zoom aplicado).
> `hud_surf` permanece como a tela nativa (necessário para escalar coordenadas do mouse).
