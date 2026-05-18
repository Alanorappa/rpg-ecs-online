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

#### Preocupação levantada: tick rate baixo quebra a visualização?

Com 20 ticks/s (50ms por tick) e **interpolação no cliente**, o visual é fluido a 60fps.
O cliente não espera o servidor para animar — interpola entre posições confirmadas.
A **confirmação do servidor** é o que vale para gameplay; a **animação local** é cosmética.

#### Interceptar (Guerreiro — dash ao alvo)

**Problema:** sem interpolação, o personagem "teleportaria" a cada 50ms.

**Solução — Client-side prediction:**
```
1. Jogador usa Interceptar
2. Cliente anima o dash imediatamente (não espera servidor)
3. Servidor valida: alvo ainda existe? range ok? CD ok? em combat?
4. Servidor envia ENTITY_MOVE com posição confirmada
5. Cliente reconcilia se posição diferir (raro com latência < 100ms)
```
Resultado esperado: dash visualmente igual à versão offline.

**Implementação:** `CAST_SKILL {sid:"interceptar", tid:X}` → servidor envia `ENTITY_MOVE` para o caster + `SKILL_RESULT` com efeitos.

---

#### Tiro Repulsivo (knockback + stun)

**Problema:** o alvo pode estar em posição diferente entre cliente e servidor no momento do knockback. Se o cliente calculasse o destino, teria inconsistências entre jogadores.

**Solução — Knockback autoritativo:**
```
1. Servidor calcula: tile de destino do knockback (direção + distância)
2. Servidor envia ENTITY_MOVE {eid:alvo, tx, ty} + EFFECT_APPLIED {stun}
3. Cliente recebe o destino e anima o knockback suavemente até esse tile
```
O cliente **nunca decide** onde o alvo vai parar — só anima o destino recebido.
Visual: a animação de knockback roda no cliente a 60fps sobre o tile confirmado pelo servidor.

---

#### Tiro Múltiplo e Pirofagia (cone na direção do mouse)

**Problema:** aiming é feito com o mouse no cliente. Com latência, o servidor pode ter posições de inimigos ligeiramente diferentes do que o cliente via no momento do cast.

**Solução — Lag compensation com timestamp:**
```
1. Cliente envia: CAST_SKILL {sid:"tiro_multiplo", dir_x, dir_y, ts:T}
2. Servidor rebobina o estado do mundo para o tick mais próximo de T
   (usando WorldServer.get_snapshot_at — histórico de até 200ms)
3. Servidor calcula quais entidades estavam no cone NAQUELE momento
4. Aplica dano e envia SKILL_RESULT
```
Tolerância de direção: ±10° para compensar latência residual.
Isso é idêntico ao que Counter-Strike usa para headshots — padrão da indústria.

---

#### Camuflagem — A mais crítica de todas

**Problema:** uma falha aqui vaza posição de jogadores invisíveis para o cliente inimigo. Um cliente modificado poderia ler esses dados e ver jogadores invisíveis.

**Protocolo rigoroso:**
```
Jogador ativa Camuflagem:
  → Servidor: is_visible = False no componente CombatState
  → Servidor: REMOVE o player do pacote AOI_UPDATE de todos os outros
  → Outros clientes: deletam a entidade da cena (ENTITY_DESPAWN)
  → Nenhum dado de posição, HP, animação é enviado enquanto invisível

Camuflagem termina (tempo, ataque, dano recebido):
  → Servidor: is_visible = True
  → Servidor: RE-INCLUI o player no AOI de quem está no range
  → Clientes próximos recebem ENTITY_SPAWN com posição atual
```
**Garantia:** o cliente inimigo literalmente não tem dados do jogador invisível.
Não é uma questão de "não mostrar" — os dados não chegam ao cliente.

Único caso especial: o **próprio jogador invisível** recebe seus próprios dados normalmente
(para ele ver a si mesmo, UI de camuflagem ativa, etc.).

---

#### Bola de Fogo / Projéteis (Flecha Reiterada, Picada do Escorpião)

**Problema:** projétil precisa parecer fluido (animação contínua) mas o hit é calculado no servidor.

**Solução — Projétil fantasma no cliente:**
```
1. Servidor envia PROJECTILE_SPAWN {pid, sid, origin_x, origin_y, tid, speed}
2. Cliente: simula o projétil localmente com a mesma física (animação)
3. Servidor: calcula hit real no tick correto
4. Servidor envia PROJECTILE_HIT {pid, hit:true/false, end_x, end_y}
5. Cliente: toca animação de impacto (ou desvio se miss)
```
Diferença visual entre animação local e confirmação: 50–100ms — imperceptível.

---

#### Skills sem impacto significativo online

| Skill | Por quê funciona igual |
|-------|----------------------|
| Golpe Poderoso, Executar, Impacto | Instantâneas — servidor calcula, cliente recebe `COMBAT_RESULT` |
| Nova Congelante | AOE instantâneo — `SKILL_RESULT` com lista de afetados |
| Polimorfia, Calcinar | Estado/dano instantâneo |
| Bloco de Gelo | Flag booleano — `EFFECT_APPLIED {effect:"ice_block"}` |
| Canção de Ninar | Channeling: servidor envia `EFFECT_APPLIED {sleep}` a cada tick |
| Canção da Inspiração | Buff: `EFFECT_APPLIED` no caster |
| Só um Gole | Buff local: `EFFECT_APPLIED` |
| Vitória Iminente | Carga: `STATS_UPDATE` quando carga acumula |

---

#### Resumo: o que o cliente pode fazer vs. não pode

| Pode (cosmético/conforto) | Não pode (gameplay) |
|---------------------------|---------------------|
| Animar dash do Interceptar antes do servidor confirmar | Decidir se o dash acertou |
| Simular projétil voando localmente | Decidir se o projétil acertou |
| Interpolar movimento entre ticks | Calcular dano |
| Mostrar barra de cast | Aplicar efeito de status |
| Animar knockback suavemente | Decidir destino do knockback |
| Apontar cone (Pirofagia/Tiro Múltiplo) | Decidir quem está no cone |

### 10. Deploy (produção)
- **Plataforma recomendada:** Fly.io ou Railway (Docker, escala automática, custo baixo)
- **Infraestrutura dev:** `python server/main.py` local
- Porta padrão: **8765** (configurável via `--port`)
- `requirements_server.txt` — dependências mínimas do servidor

---

### 11. Como testar localmente (múltiplos clientes)

Todos os processos rodam na mesma máquina. Abrir 3 terminais:

```bash
# Terminal 1 — Servidor
cd rpg_ecs_online
python server/main.py

# Terminal 2 — Cliente A (conta "teste")
cd rpg_ecs_online
python main.py --user teste --password 123456

# Terminal 3 — Cliente B (conta "teste2")
cd rpg_ecs_online
python main.py --user teste2 --password 123456
```

**Contas de teste** criadas automaticamente no primeiro `python server/main.py`:
| Usuário | Senha | Classe |
|---------|-------|--------|
| `teste` | `123456` | Guerreiro |
| `teste2` | `123456` | Mago |

Para adicionar mais contas de teste: `server/auth.py` → função `_seed_test_accounts()`.

**Deletar banco e recomeçar:** apagar `data/game.db` — será recriado na próxima inicialização.

**Ver log do servidor em tempo real:** o servidor imprime conexões, logins, moves e combate no terminal.

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
| Integração network → game.py | ✅ Marco 1 | `client/network.py` + `game.py` |
| Entidades de jogadores no servidor | ✅ Marco 1 | `server/world_server.py` |
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
