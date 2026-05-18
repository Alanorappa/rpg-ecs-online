# Arquitetura Online — Decisões e Referência

> Documento vivo. Atualizar sempre que uma decisão arquitetural for tomada.
> Criado em: 2026-05-18

---

## Visão geral

RPG Tibia/WoW-style com servidor autoritativo em Python.
Clientes Pygame se conectam via WebSocket e recebem o estado do mundo por ticks.

```
[Cliente Pygame] ──WebSocket──► [Servidor Python / asyncio]
                 ◄──────────────      ECS headless
                                      SQLite (dev) → PostgreSQL (prod)
```

---

## Decisões arquiteturais

### 1. Modelo de mundo — Híbrido
- **Open world** compartilhado até 500 jogadores/servidor
- **Instâncias** para dungeons, raids, áreas de boss/mobs raros
  - Instância nasce ao primeiro jogador entrar, morre ao último sair
  - Gerenciada por `server/zone_manager.py` (a implementar)
- **PvP** apenas em zonas contestadas (flag por zona no mapa)
- MVP inicial: servidor único monolítico (Opção A), escalar depois

### 2. Protocolo de rede
- **Transporte:** WebSocket (TCP) — suficiente para tile-based
- **Formato:** JSON para desenvolvimento → MessagePack antes do lançamento
- **Versão do protocolo:** campo `PROTOCOL_VERSION` em `shared/constants.py`
  - Servidor rejeita clientes com versão incompatível (`LOGIN_ERROR: version_mismatch`)
- **Sequência:** campo `seq` (int crescente por conexão) em todo pacote
- **Timestamp:** campo `ts` (ms epoch) para lag compensation

### 3. Tick rate — 20 ticks/s (50ms)
- Movimento, combate, skills: **20 ticks/s**
- Cliente roda a 60 fps com **interpolação** entre ticks → visual fluido
- **Client-side prediction** para movimento do próprio jogador
  - Cliente move imediatamente ao input
  - Servidor confirma/corrige na resposta
- Skills com aiming (Pirofagia, Tiro Múltiplo): **lag compensation com timestamp**
  - Cliente envia direção + `ts`
  - Servidor rebobina estado até `ts` (janela máxima: `LAG_COMP_WINDOW_MS = 200ms`)

### 4. Servidor autoritativo
O servidor é a **única fonte de verdade**. O cliente nunca decide:
- Dano (calculado por `damage_calculator.py` no servidor)
- Resultado de hit/miss/crit/dodge
- Posição final após knockback
- Drops de loot
- Estado de invisibilidade de outros jogadores

### 5. Area of Interest (AOI)
- Raio: **15 tiles** — coincide com `FOG_RADIUS` do cliente
- Servidor envia apenas entidades dentro do AOI de cada jogador
- Jogadores invisíveis (Camuflagem) **não aparecem** no pacote AOI de outros
- `AOI_UPDATE` é o pacote mais frequente — contém apenas **deltas** (o que mudou)

### 6. Persistência
| Fase | Banco | Motivo |
|------|-------|--------|
| Desenvolvimento | SQLite (`data/game.db`) | Zero config, portátil |
| Produção | PostgreSQL | Concorrência real, backup |

Migração: trocar string de conexão no `server/auth.py` — schema SQL é compatível.
ORM: SQLAlchemy (a adicionar antes da migração para prod).

### 7. Autenticação
- **Dev:** username + SHA-256(password) → SQLite
- **Prod:** mesma base + HTTPS obrigatório (WebSocket Secure / `wss://`)
- Session token UUID gerado no `LOGIN_OK`, usado para reconexão
- TODO: adicionar campo `email` na tabela `accounts` antes do lançamento

### 8. Fog of War + segurança
- FOG de 15 tiles no cliente → AOI de 15 tiles no servidor
- Servidor **não envia** dados de entidades fora do AOI
- Camuflagem (`is_visible=False`): servidor remove player do pacote AOI de todos os outros
  - Nenhum dado de posição, animação ou HP vaza

### 9. Skills com implicações online

| Skill | Tratamento necessário |
|-------|-----------------------|
| **Interceptar** | Client prediction: cliente anima imediatamente, servidor confirma posição |
| **Tiro Múltiplo** | Lag compensation: cliente envia `dir_x/dir_y` + `ts`, servidor valida no snapshot |
| **Pirofagia** | Igual Tiro Múltiplo |
| **Tiro Repulsivo** | Knockback calculado no servidor; cliente anima destino recebido |
| **Camuflagem** | Servidor remove do AOI de outros; só o próprio player recebe `is_visible=False` |
| **Bola de Fogo / flechas** | Servidor envia `PROJECTILE_SPAWN`; cliente simula localmente; `PROJECTILE_HIT` confirma |
| **Canção de Ninar** | Canal com ticks; servidor envia `EFFECT_APPLIED` a cada tick de sleep |

### 10. Deploy (produção)
- **Plataforma recomendada:** Fly.io ou Railway (Docker, escala automática, custo baixo)
- **Infraestrutura dev:** `python server/main.py` local
- Porta padrão: **8765** (configurável via `--port`)
- `requirements_server.txt` — dependências mínimas do servidor

---

## Protocolo de mensagens — resumo

Ver `shared/messages.py` para definição completa de cada payload.

| Direção | Tipo | Quando |
|---------|------|--------|
| C→S | `LOGIN` | Conectou, envia credenciais |
| S→C | `LOGIN_OK` | Autenticado — inclui `WORLD_STATE` inicial |
| S→C | `LOGIN_ERROR` | Credenciais inválidas ou versão incompatível |
| C→S | `MOVE` | Jogador quer mover 1 tile |
| S→C | `ENTITY_MOVE` | Broadcast de movimento (inclui correção de posição) |
| C→S | `AUTO_ATTACK` | Iniciar/parar auto-attack em `tid` |
| S→C | `COMBAT_RESULT` | Resultado de hit: dano, outcome, HP após |
| C→S | `CAST_SKILL` | Usar skill com `sid`, `tid`, direção e `ts` |
| S→C | `CAST_START` / `CAST_COMPLETE` | Barra de cast visível para outros |
| S→C | `SKILL_RESULT` | Efeitos aplicados pela skill |
| S→C | `PROJECTILE_SPAWN` | Projétil criado — cliente simula localmente |
| S→C | `PROJECTILE_HIT` | Confirmação de hit/miss do projétil |
| S→C | `EFFECT_APPLIED/REMOVED` | Status effect em qualquer entidade |
| S→C | `STATS_UPDATE` | HP/MP/rage/concentration mudou |
| S→C | `AOI_UPDATE` | Delta do tick: spawns, despawns, moves, stats |
| S→C | `ENTITY_SPAWN/DESPAWN` | Entidade entrou/saiu do AOI |
| C↔S | `PING/PONG` | Medição de latência (a cada 5s) |
| C→S | `CHAT_SEND` / S→C `CHAT_MESSAGE` | Chat world/local/party |

---

## Estado de implementação

| Componente | Status | Arquivo |
|------------|--------|---------|
| Protocolo de mensagens | ✅ completo | `shared/messages.py` |
| Constantes compartilhadas | ✅ completo | `shared/constants.py` |
| Loop de ticks (ECS headless) | ✅ estrutura | `server/world_server.py` |
| Gerenciador de sessões | ✅ estrutura | `server/session.py` |
| Autenticação + banco | ✅ funcional | `server/auth.py` |
| Servidor WebSocket | ✅ funcional | `server/main.py` |
| Cliente de rede (Pygame) | ✅ estrutura | `client/network.py` |
| Integração network → game.py | 🔲 pendente | `client/network.py` + `game.py` |
| Sistemas de lógica no servidor | 🔲 pendente | `server/world_server.py` |
| Instâncias (dungeons/raids) | 🔲 pendente | `server/zone_manager.py` |
| Lag compensation | 🔲 parcial | `server/world_server.py` |
| Client-side prediction | 🔲 pendente | `client/` |

---

## Próximos passos (em ordem)

1. **Integrar `NetworkClient` no `game.py`** — substituir input local por protocolo
2. **Implementar `MovementSystem` no servidor** — validar moves, broadcast AOI
3. **Implementar `CombatSystem` no servidor** — auto-attack, `COMBAT_RESULT`
4. **Implementar `SkillSystem` no servidor** — skills simples primeiro (instantâneas)
5. **`AOISystem`** — filtrar `AOI_UPDATE` por range real
6. **Teste de dois clientes** — dois jogadores se vendo e se atacando
7. **`EnemyAISystem`** no servidor — mobs funcionando em multiplayer
8. **Instâncias** — `zone_manager.py`
