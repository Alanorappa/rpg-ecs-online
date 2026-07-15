# Battlefield PvP (Estilo MOBA) — Documento de Design

> Modo de jogo instanciado dentro do MMORPG, onde o player usa seu próprio personagem (classe, level e itens do overworld) em uma partida 5v5 balanceada por faixas de level/ilvl, com progressão temporária via buffs e honra.

---

## 1. Visão Geral

- O player entra numa **fila** e é pareado em times de 5 (5v5).
- A partida acontece em um **mapa instanciado** (torres, minions, jungle, NPCs vendedores).
- O player **não reseta** level/itens/skills — ele entra com seu personagem real do overworld (**Modelo 1: enxuto**).
- O desbalanceamento entre players de levels diferentes é compensado por:
  - Faixas (brackets) de level na fila.
  - Balanceamento de **item level (ilvl)** médio entre os times.
  - **Buffs temporários** comprados com uma moeda exclusiva da instância (Honra).
  - **Drops de itens exclusivos** de mobs da jungle, válidos só dentro da instância.
- Matar minions, jungle mobs e players concede **XP permanente** e **Honra** (moeda temporária).
- Existem **quests** dentro da instância, reaproveitando conteúdo já existente do jogo.

---

## 2. Decisão de Design: Level/Itens do Player na Instância

Foram considerados 3 modelos:

1. **Leva tudo do personagem principal** (escolhido ✅)
   - Sem necessidade de snapshot/state paralelo.
   - Desbalanceamento resolvido via matchmaking (brackets + ilvl) e buffs/drops in-game.
2. Reset total (level 1 na instância) — descartado, exigiria sistemas de state paralelo (inventário, level, gold temporários).
3. Híbrido (classe mantida, level/itens zerados) — descartado pelo mesmo motivo do modelo 2, mais complexo que o necessário dado o design de compensação already definido.

**Motivo da escolha:** o Modelo 1 reaproveita 100% dos sistemas já existentes (classes, itens, XP, skills) e todo o balanceamento é resolvido em cima da fila e de mecânicas *dentro* da partida, sem exigir arquitetura nova de state paralelo.

---

## 3. Matchmaking / Fila

### 3.1 Faixas de Level (Brackets)

Players são agrupados em faixas fixas, ex:
- 11–20
- 21–30
- 31–40
- (e assim por diante)

### 3.2 Balanceamento entre Times

Ao formar dois times de 5, o sistema calcula:

```python
def team_power_score(team):
    avg_level = mean(p.level for p in team)
    avg_ilvl = mean(p.avg_item_level for p in team)
    return avg_level, avg_ilvl

def is_balanced(team_a, team_b, max_level_diff=1):
    lvl_a, ilvl_a = team_power_score(team_a)
    lvl_b, ilvl_b = team_power_score(team_b)
    if abs(lvl_a - lvl_b) > max_level_diff:
        return False
    # se a diferença de level for aceitável, o ilvl serve de fator de compensação
    # (time com level médio maior deve ter ilvl médio maior, senão o pareamento
    # não é considerado justo e o sistema tenta outra combinação)
    return True
```

- Diferença máxima de level médio entre os times: **1 nível** (dentro do bracket).
- O item level médio deve ser proporcional ao level médio de cada time (compensação).
- **V1 (recomendado para começar):** greedy sort + swap para montar os times mais equilibrados dentro do pool de tickets disponíveis. Não é necessário um algoritmo complexo (tipo TrueSkill/Elo) na primeira versão.

### 3.3 Fluxo da Fila

```
Player entra na fila (QueueTicket: player_id, level, avg_ilvl, entered_at)
    ↓
Scheduler roda periodicamente
    ↓
Agrupa players do mesmo bracket
    ↓
Monta combinações de 5v5 minimizando diferença de score entre os times
    ↓
Match formado → aciona o InstanceManager
```

---

## 4. Instanciamento

- Cada partida roda em um **World/registry ECS isolado** (não precisa de processo/servidor separado no início).
- O `InstanceManager` cria a instância, carrega o mapa (torres, shops, spawns de minion/jungle) e move os 10 players para esse contexto.
- Ao fim da partida (vitória/derrota/desistência), a instância é destruída (`teardown`) e os players retornam ao overworld com seu estado normal.

```
Match formado
    ↓
InstanceManager.create_instance(match)
    ↓
Cria BattlefieldInstance isolada (novo World ECS)
    ↓
Carrega mapa: torres, shops, spawners de minion/jungle
    ↓
Move os 10 players pra essa instância (troca de contexto ECS)
    ↓
Loop principal do jogo dá tick nessa instância junto com o resto
    ↓
Fim da partida (vitória/derrota/desistência)
    ↓
teardown() → libera memória, players voltam pro overworld com estado original intacto
```

---

## 5. Sistemas de Gameplay Dentro da Instância

### 5.1 Honra (moeda temporária)

- Ganha matando minions, jungle mobs e players.
- Só existe dentro da instância (não persiste no overworld).
- Usada para comprar buffs temporários com um NPC vendedor.

### 5.2 Buffs Temporários (duração ~5 min)

Dois tipos:

1. **Comprados com Honra** (escolha do player), ex:
   - Atributo bruto (dano, resistência, etc).
   - Efeito adicional numa skill.
   - Buff de counter, ex: resistência mágica se o time inimigo tem mais magos.
   - Buff para minions ou torres.
2. **Conquistados via objetivo de mapa** (estilo dragão/baron do LoL):
   - Buff de time inteiro ao derrotar um jungle boss específico.

Ao longo de uma partida, o player deve acumular algo em torno de **6 buffs**.

### 5.3 Drops de Jungle (compensação de ilvl)

- Mobs da jungle dropam **itens exclusivos da instância** (não saem para o overworld).
- Servem para o player com ilvl mais baixo "recuperar" poder dentro da própria partida.
- Como são exclusivos da instância, podem ser **mais fortes que itens equivalentes do overworld** sem quebrar a economia principal do jogo.
- Somem ao fim da partida.

### 5.4 Penalidade por Death Streak

- Se o player morrer 3 vezes seguidas sem conseguir um kill, ele para de conceder XP/Honra por matar o inimigo (ou fica em cooldown) até:
  - Conseguir um kill, ou
  - Passar um tempo de cooldown.
- Evita o ciclo de "rich get richer" sem precisar de XP de consolação.

### 5.5 XP Permanente

- Matar minions, jungle mobs e players concede XP real, que reflete no personagem do overworld.
- Motiva o player a jogar o modo além da diversão da partida em si.

### 5.6 Quests da Instância

- Reaproveitam conteúdo/triggers já existentes no jogo, ex:
  - Vencer 3 batalhas.
  - Obter item específico de drop de jungle boss.
- Devem ser curtas (partida dura ~15-20 min), não competindo com o ritmo da partida.

---

## 6. Componentes ECS (referência inicial)

> Esses componentes complementam os já existentes (Level, Inventory, Skills, etc). A ideia é que a maioria seja anexada à entidade do player **apenas enquanto ele estiver na instância**, e removida no teardown.

```python
# --- Fila / Matchmaking ---

@component
class QueueTicket:
    player_id: int
    level: int
    avg_item_level: float
    entered_at: float
    bracket: str  # ex: "11-20"

@component
class MatchAssignment:
    match_id: str
    team: Literal["A", "B"]


# --- Instância ---

@component
class InstanceMember:
    instance_id: str
    original_world_ref: Any  # referência pro overworld, pra devolver o player no teardown

@component
class InstanceMap:
    towers: list[EntityId]
    jungle_camps: list[EntityId]
    shops: list[EntityId]
    minion_spawners: list[EntityId]


# --- Honra / Economia temporária ---

@component
class HonorCurrency:
    amount: int  # só existe dentro da instância, zera no teardown


# --- Buffs ---

@component
class InstanceBuff:
    buff_id: str
    source: Literal["purchased", "objective"]
    duration_remaining: float
    effect_type: Literal["attribute", "skill_effect", "minion_buff", "tower_buff"]
    effect_payload: dict  # dados específicos do efeito


# --- Drops exclusivos da instância ---

@component
class InstanceItem:
    item_id: str
    ilvl: int
    exclusive_to_instance: bool = True  # não pode sair pro overworld


# --- Penalidade de death streak ---

@component
class DeathStreakPenalty:
    consecutive_deaths: int
    reward_locked: bool
    cooldown_remaining: float


# --- Quests da instância ---

@component
class InstanceQuest:
    quest_id: str
    objective_type: Literal["win_battles", "kill_jungle_boss", "collect_item"]
    progress: int
    target: int
    reward: dict


# --- Objetivos de mapa (estilo dragão/baron) ---

@component
class JungleBoss:
    boss_id: str
    respawn_timer: float
    team_buff_on_kill: str  # referência a um InstanceBuff aplicado ao time
```

---

## 7. Fluxo de Dados Completo (Fila → Fim de Partida)

```
1. Player entra na fila
   → cria QueueTicket (level, avg_ilvl, bracket)

2. Scheduler de matchmaking roda periodicamente
   → agrupa por bracket
   → monta 5v5 minimizando diferença de team_power_score
   → cria MatchAssignment pros 10 players

3. InstanceManager.create_instance(match)
   → cria BattlefieldInstance (World ECS isolado)
   → carrega InstanceMap (torres, shops, jungle, minion spawners)
   → anexa InstanceMember, HonorCurrency (zerada) aos 10 players
   → move os players pro contexto da instância

4. Durante a partida:
   → minions/jungle/players mortos concedem XP (permanente) + Honra (temporária)
   → HonorCurrency é usada em shops pra comprar InstanceBuff
   → JungleBoss dropa InstanceItem e/ou concede InstanceBuff de time
   → DeathStreakPenalty bloqueia recompensas após 3 mortes seguidas
   → InstanceQuest acompanha objetivos (vencer batalhas, itens, etc)

5. Fim da partida (vitória/derrota/desistência)
   → aplica recompensas finais (XP, itens de recompensa do RPG, se houver)
   → InstanceManager.teardown(instance)
       → remove InstanceMember, HonorCurrency, InstanceBuff, InstanceItem, DeathStreakPenalty
       → devolve players ao overworld com estado original intacto
```

---

## 8. Roadmap Sugerido de Implementação

1. **Protótipo de instanciamento** (sem fila): comando de debug que joga jogadores fixos numa instância nova. Validar isolamento do World ECS (torres, minions, jungle rodando só ali).
2. **Fila simples** (sem balanceamento de score, só agrupa por bracket).
3. **Balanceamento de times** (team_power_score, greedy sort + swap).
4. **Honra + Buffs comprados.**
5. **Jungle bosses + buffs de objetivo + drops de itens exclusivos.**
6. **Death streak penalty.**
7. **Quests da instância.**
8. Iteração de balanceamento de buffs entre si (trabalho contínuo de game design, não só implementação).

---

## 9. Riscos / Pontos de Atenção para o Futuro

- **Variância de poder dos itens dentro do mesmo bracket:** level sozinho pode não bastar; ilvl médio ajuda, mas vale monitorar em playtests se é suficiente.
- **Meta de buffs:** cuidado para buffs não convergirem sempre pra mesma escolha "óbvia" — exige balanceamento contínuo pós-lançamento do modo.
- **Quests devem ser curtas:** partida de 15-20 min não comporta quests longas, sob risco de competir com o ritmo do próprio modo.
- **Escopo:** resistir à tentação de adicionar muito conteúdo (heróis/kits novos) antes de validar que o loop base (fila → instância → partida → recompensas) é divertido e estável.
