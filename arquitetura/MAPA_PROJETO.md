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
│   └── network.py                  ← NetworkClient: WebSocket em background thread
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
| `core_systems.py` | NOVO (COMPARTILHADO) | `apply_effect()` + `StatusEffectSystem` base sem Pygame; importado por cliente e servidor |
| `game.py` | MODIFICADO | Handlers online, `_use_skill_visual_only`, `_handle_net_message` |
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
3. `game.py` → handler em `_handle_net_message`
4. `ARQUITETURA_ONLINE.md` → atualizar tabela de mensagens
