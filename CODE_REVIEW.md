# Code Review — RPG ECS Online
> Revisão como Desenvolvedor de Jogos ECS Especialista  
> Data: 2026-05-24 | Branch: `online`  
> Arquivos lidos: `world_server.py`, `session.py`, `server_death_handler.py`, `damage_calculator.py`, `shared/messages.py`, `shared/constants.py`, `components.py`, `systems.py` (parcial), `core_systems.py`, `server/auth.py`, `arquitetura/*`

---

## Sumário executivo

O projeto tem uma base sólida de ECS e boa separação cliente/servidor no geral. Os problemas críticos mais sérios estão em **segurança** (movement sem validação, client-trusting HP/gold), **duplicação de lógica** (CombatStateSystem copiado verbatim), e **acoplamento implícito** (singleton `last_outcome`, inner class `_ServerSFX`, dynamic attributes para inventário). Os problemas de escalabilidade mais urgentes são o **N² AOI sweep** e o **busy-wait na tick loop**.

---

## 🔴 CRÍTICOS — Bug ativo ou risco de segurança

---

### C1 — `move_player` não valida walkability ✅ RESOLVIDO (commit f8d3777)

**Arquivo:** [server/world_server.py](server/world_server.py) linha 498  
```python
# TODO: validar walkability com tilemap
```
**Problema:** Qualquer cliente pode mover-se para tiles sólidos, dentro de paredes, ou atravessar obstáculos. O servidor apenas valida que o delta é ≤1 tile.

**Impacto:** Exploit de movimento, bypass de obstáculos, teleporte para dentro de objetos.

**Fix:**
```python
# Em move_player, após validar dx/dy:
if not is_tile_walkable(eid, tx, ty, from_tx=tm.current_tile_x, from_ty=tm.current_tile_y):
    # Envia correção de posição de volta para o cliente
    return False
```

---

### C2 — `client_max_hp` e `client_ap` confiados sem validação para personagens novos ✅ RESOLVIDO (commit f8d3777)

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 308–318  
```python
_client_hp = int(char_data.get("client_max_hp", 0))
_client_ap = float(char_data.get("client_ap", 0))
if not _stats:          # <-- personagem sem save_json
    if _client_ap > 0:
        cs.base_attack_power = int(_client_ap)
    if _client_hp > 0:
        cs.max_hp = _client_hp
```
**Problema:** Para um personagem novo (sem `stats_json`), o servidor aceita qualquer `client_max_hp` enviado no payload de login. Um cliente malicioso pode enviar `client_max_hp=999999` e ter HP ilimitado até o primeiro autosave.

**Fix:** Calcular `max_hp` exclusivamente no servidor a partir dos atributos base + equipamento. Remover `client_max_hp`/`client_ap` do protocolo de login ou tratá-los apenas como hint para a primeira sincronização com cap razoável (ex: `max(server_calc, min(client_hp, server_calc * 1.5))`).

---

### C3 — Gold dado como client-autoritativo em `_build_save_merge` ✅ RESOLVIDO (commit fc4b311)

**Arquivo:** [server/session.py](server/session.py) (lógica de save)  
**Problema:** O gold do player é tratado como autoritativo do cliente no merge do SAVE_STATE. Porém o servidor já mantém `Wallet.gold` server-side nos sistemas de loja (`process_shop_buy`) e loot (`request_loot`). Há dois valores de gold: o do servidor (correto) e o do cliente (mentiroso). Ao fazer SAVE_STATE, o cliente poderia enviar gold inflado.

**Fix:** Gold deve ser **exclusivamente server-autoritativo**. O save sempre usa `wallet.gold` do ECS do servidor. Remover gold do payload de SAVE_STATE.

---

### C4 — `last_outcome` é estado mutável global ✅ RESOLVIDO (commit 36a7385)

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 637–648  
```python
_real_outcome = getattr(
    getattr(__import__("systems", fromlist=["_svc"]), "_svc", {}).get("combat"),
    "last_outcome", "hit"
)
```
**Problema:** O outcome do último `deal_damage()` fica armazenado como atributo `last_outcome` no singleton `CombatSystem`. Isso cria:
1. **Race condition**: se dois ataques processarem no mesmo tick, o primeiro outcome é sobrescrito antes de ser lido.
2. **__import__ em runtime**: terrível para legibilidade e performance.
3. **Coupling implícito**: o servidor acessa estado interno de um sistema do cliente.

**Fix:** `deal_damage()` deve retornar `(dead: bool, outcome: str)`. Ou criar `DamageResult(dead, outcome, damage)`.

---

### C5 — Inventário pendente como atributos dinâmicos no WorldServer ✅ RESOLVIDO (commit 67e49be)

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 1261–1303  
```python
pending_key = f"_pending_inv_{session_id}"
setattr(self, pending_key, pending + 1)
# ...
delattr(self, key)
```
**Problema:** Usa `setattr`/`getattr`/`delattr` para armazenar dados de sessão como atributos do objeto. Se o jogador desconectar sem chamar `confirm_inventory_save`, o atributo fica pendurado no WorldServer para sempre (leak de memória por sessão).

**Fix:**
```python
# __init__:
self._pending_inv: dict[str, int] = {}

# process_shop_buy:
self._pending_inv[session_id] = self._pending_inv.get(session_id, 0) + 1

# confirm_inventory_save:
self._pending_inv.pop(session_id, None)

# despawn_player: limpar também
self._pending_inv.pop(session_id, None)
```

---

## 🟡 ALTOS — Manutenção, duplicação, ou performance degradada

---

### A1 — CombatStateSystem duplicado verbatim em world_server.py

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 1626–1676  
```python
# CombatStateSystem do servidor: in_combat timer + rage decay + HP5 regen
# Cópia exata do CombatStateSystem offline (systems.py:919-1022)
```
**Problema:** A lógica de `in_combat` timer, rage decay e HP5 regen está duplicada entre `systems.py` e `world_server.py`. Qualquer mudança de balanceamento (ex: mudar `RAGE_DECAY_INTERVAL` de 3s para 2s) tem que ser feita em dois lugares.

**Fix:** Mover para `core_systems.py` (sem Pygame, já existe) como `class CombatStateSystem(System)`. O servidor instancia e chama. Elimina ~50 linhas duplicadas.

---

### A2 — N² AOI sweep em `session.py` por tick

**Arquivo:** [server/session.py](server/session.py) linhas 717–726  
```python
for mob_eid in list(self.world_server._mob_eids):   # O(mobs)
    if mob_eid in session.known_eids:
        continue
    # ... in_aoi check
```
**Problema:** Para cada jogador, a cada tick, itera TODOS os mobs do servidor para encontrar os que entraram no AOI. Com 10 players × 200 mobs × 20 ticks/s = **40.000 iterações/s**.

**Fix:** Usar **spatial hash** (grade de tiles 16×16) para lookup O(1) de entidades em AOI. Ou manter `_mob_eids_by_tile: dict[tuple, set[int]]` atualizado a cada movimento de mob.

---

### A3 — Tick loop busy-wait com `asyncio.sleep(0)`

**Arquivo:** [server/world_server.py](server/world_server.py) linha 1565  
```python
await asyncio.sleep(0)   # ~50ms de busy-wait entre ticks
```
**Problema:** O loop faz `await asyncio.sleep(0)` centenas de vezes entre cada tick (50ms), consumindo CPU desnecessariamente e saturando o event loop.

**Fix:**
```python
else:
    sleep_time = next_tick - time.perf_counter()
    if sleep_time > 0.001:
        await asyncio.sleep(sleep_time)
    else:
        await asyncio.sleep(0)
```

---

### A4 — `_ServerSFX` definida como inner class dentro de `_load_map()`

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 155–189  
**Problema:** A classe `_ServerSFX` (override do `StatusEffectSystem` para emitir `COMBAT_RESULT`) é re-definida toda vez que `_load_map()` é chamada. É um padrão opaco: quem lê `_load_map()` encontra uma definição de classe no meio da lógica de inicialização.

**Fix:** Extrair para `server/server_sfx.py` ou topo de `world_server.py` como `class ServerStatusEffectSystem(StatusEffectSystem)`.

---

### A5 — `_lookup_item_value` instancia todos os factories por venda

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 1344–1372  
```python
for _entry in _LT.values():
    _factory = _entry.get("factory")
    if callable(_factory):
        _obj = _factory()                   # instancia item só p/ checar nome
        if getattr(_obj, "name", None) == item_name:
```
**Problema:** Para validar o preço de venda, itera todos os loot_tables e merchant_data instanciando cada factory até achar o nome. Com 100 itens no catálogo = 100 objetos criados e descartados por venda.

**Fix:** Construir um `_ITEM_VALUE_CACHE: dict[str, int]` na inicialização do WorldServer (uma vez).

---

### A6 — `process_shop_buy` chama `factory()` duas vezes com scan linear

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 1242, 1272  
```python
entry = next((e for e in stock if e["factory"]().name == item_name), None)
# ...
item_obj = entry["factory"]()
```
**Problema:** Scan linear O(items_na_loja) com instanciação de cada item para achar por nome. Depois chama a factory de novo para obter o objeto final. Duas passagens desnecessárias.

**Fix:** Pre-indexar shops por nome: `_SHOP_INDEX: dict[str, dict[str, entry]]` construído em startup.

---

### A7 — `_tick()` é um método de 400+ linhas (God Method)

**Arquivo:** [server/world_server.py](server/world_server.py) linha 1567+  
**Responsabilidades misturadas:** immunity decay → snapshots → system.update() → aggro detection → CombatStateSystem → ActiveRegen → skill processing → player attacks → mob death sweep → death handling → XP → loot → corpse decay → mob detection → movement detection → projectile detection → delta collection.

**Fix:** Quebrar em submétodos nomeados:
```python
def _tick(self, dt):
    self._update_respawn_immunity(dt)
    self._run_ecs_systems(dt)
    self._detect_aggro_sounds()
    self._update_combat_state(dt)     # ← mover para CombatStateSystem
    self._process_active_regen(dt)    # ← mover para core_systems
    self._process_skill_requests()
    self._process_player_attacks(dt, ...)
    self._process_deaths()
    self._process_spawns_and_moves()
    self._collect_and_dispatch()
```

---

### A8 — `get_session_id_for_player` é O(players), chamado em toda morte

**Arquivo:** [server/world_server.py](server/world_server.py) linha 866  
```python
for sid, eid in self._player_eids.items():
    if eid == player_eid:
        return sid
```
**Fix:** Manter reverse map: `self._player_session_by_eid: dict[int, str] = {}` atualizado em `spawn_player`/`despawn_player`.

---

### A9 — Mob attacker identification é O(mobs) por player por tick

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 673–679  
Para cada player que recebeu dano, itera todos os mobs do servidor para achar o agressor via `AIControlled.target_eid`. Com 10 players recebendo dano e 200 mobs = 2000 iterações extras por tick.

**Fix:** Quando `EnemyAISystem` executa o ataque, enfileirar `{"attacker_mob": mob_eid, "target_player": player_eid}` diretamente. Ou manter `_mob_aggro_targets: dict[int, int]` (mob_eid → player_eid) atualizado pelo AI.

---

## 🔵 MÉDIOS — Débito técnico e inconsistências

---

### M1 — `_store_snapshot` usa `list.pop(0)` em vez de `deque`

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 1956–1957  
```python
if len(self._snapshots) > SNAPSHOT_HISTORY:
    self._snapshots.pop(0)   # O(n) — desloca toda a lista
```
**Fix:** `self._snapshots: deque[tuple] = deque(maxlen=SNAPSHOT_HISTORY)` — pop automático, O(1).

---

### M2 — Snapshot armazena TODAS as entidades com TileMovement

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 1952–1955  
Lag compensation só precisa de posições de mobs. O snapshot atual inclui todos (players, mobs, projéteis).

**Fix:** Filtrar apenas `_mob_eids` no snapshot.

---

### M3 — `damage_log_fn` em `ServerDeathHandler` é parâmetro morto

**Arquivo:** [server/server_death_handler.py](server/server_death_handler.py) linha 47  
```python
self.damage_log_fn  = damage_log_fn   # sempre None — world_server é usado
```
O `world_server_server` é injetado e sempre usado. `damage_log_fn` nunca é chamado.

**Fix:** Remover o parâmetro `damage_log_fn`.

---

### M4 — `"vitoria_iminente"` hardcoded no ServerDeathHandler

**Arquivo:** [server/server_death_handler.py](server/server_death_handler.py) linhas 107–108  
```python
if _sk and _sk.skill_id == "vitoria_iminente" and _sk.max_charges > 0:
```
Lógica de "recuperar carga ao matar mob" hardcoded por ID. Já documentado como P8, mas agora está também no servidor (não só no cliente).

**Fix:** Adicionar `on_kill: "charge"` em `SKILL_CATALOG["vitoria_iminente"]`. Death handler itera skills com `on_kill` setado.

---

### M5 — `consume_despawns()` tem tipo de retorno errado

**Arquivo:** [server/server_death_handler.py](server/server_death_handler.py) linha 189  
```python
def consume_despawns(self) -> list[int]:   # ← ERRADO
```
Retorna `list[dict]` com `{"eid": int, "tx": int, "ty": int}`, não `list[int]`.

**Fix:** `def consume_despawns(self) -> list[dict]:` e adicionar `TypedDict` se quiser rigor.

---

### M6 — Factory functions de `messages.py` não são usadas consistentemente

**Arquivo:** [shared/messages.py](shared/messages.py) linhas 351–384  
Existem `make_combat_result()`, `make_entity_spawn()`, `make_stats_update()`, mas `world_server.py` e `session.py` constroem dicts manualmente. Os helpers são dead code na prática.

**Fix:** Ou usar sistematicamente (refatorar callers) ou remover os helpers e documentar o schema nos comentários que já existem.

---

### M7 — `PROJECTILE_SPAWN`/`PROJECTILE_HIT`/`PROJECTILE_DESPAWN` em MsgType não usados

**Arquivo:** [shared/messages.py](shared/messages.py) linhas 59–61  
Projéteis de mobs são enviados como `ENTITY_SPAWN(kind="mob_projectile")` e `ENTITY_DESPAWN`, nunca via os tipos dedicados de projétil.

**Fix:** Marcar como `# reservado — não implementado` ou usar os tipos dedicados.

---

### M8 — `physical_fixed` em `damage_calculator.py` é branch morta

**Arquivo:** [damage_calculator.py](damage_calculator.py) linhas 120–121  
```python
elif damage_type == "physical_fixed":
    pass      # dano final = 0
```
Se `damage_type == "physical_fixed"`, a função retorna 0. É provavelmente um stub esquecido.

**Fix:** Implementar (`total = base_ability_damage` sem componente de weapon/stats) ou remover e documentar no caller.

---

### M9 — `ActiveRegen`: variável `_healed` calculada antes da atualização do HP

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 1695–1696  
```python
_healed = min(_regen.heal_per_tick, _rcst.max_hp - _rcst.current_hp)   # cap
_rcst.current_hp = min(_rcst.max_hp, _rcst.current_hp + _regen.heal_per_tick)  # não usa _healed
```
`_healed` calcula o valor com cap, mas `current_hp` é atualizado com o valor sem cap (o `min()` no assignment garante o resultado correto, mas é enganoso). A variável `_healed` enviada ao cliente pode estar errada se `heal_per_tick` > HP faltante.

**Exemplo do bug:** max_hp=100, current_hp=98, heal_per_tick=5:  
- `_healed = min(5, 2) = 2` ← enviado ao cliente  
- `current_hp = min(100, 103) = 100` ← diff real é 2  
- Coincidentemente correto, mas `_healed` deveria ser calculado APÓS a atualização:
```python
_old_hp = _rcst.current_hp
_rcst.current_hp = min(_rcst.max_hp, _rcst.current_hp + _regen.heal_per_tick)
_healed = _rcst.current_hp - _old_hp
```

---

### M10 — `_remote_mobs_reverse_srv` e `_player_seid_by_eid` são triviais mas não documentam por quê existem

**Arquivo:** [server/world_server.py](server/world_server.py) linhas 1871–1879  
```python
def _remote_mobs_reverse_srv(self, mob_eid: int) -> int:
    return mob_eid   # identidade
```
Esses métodos existem para simetria com uma API de cliente onde os eids são mapeados. No servidor são NOPs. Devem ter um comentário explicando o padrão ou serem removidos e inlineados.

---

### M11 — `damage_calculator.py` tem side effect não declarado (RNG)

**Arquivo:** [damage_calculator.py](damage_calculator.py) — docstring do módulo  
> "Funções sem efeito colateral e sem acesso ao World."

`calculate_base_damage` chama `random.randint()` — tem efeito colateral no estado do RNG. Em testes determinísticos isso quebra a reprodutibilidade. O comentário está incorreto.

**Fix:** Atualizar docstring. Se quiser funções realmente puras, receber `rng: random.Random` como parâmetro.

---

## 🟢 BAIXOS — Limpeza e padronização

---

### B1 — Imports dentro de métodos e loops em world_server.py

Múltiplos `from components import X` dentro de métodos e até dentro de loops `for`. O Python cacheia imports mas é um smell que oculta dependências.

**Ocorrências principais:** `_tick()`, `_process_player_attacks()`, `_load_map()` (múltiplos imports no corpo).

**Fix:** Importar no topo do arquivo ou no início do método, uma vez.

---

### B2 — `ENTER_INSTANCE`, `ZONE_CHANGE`, `CAST_START/CANCEL/COMPLETE` não implementados

**Arquivo:** [shared/messages.py](shared/messages.py) linhas 39–55  
Existem no enum mas não há handler no servidor nem no cliente.

**Fix:** Adicionar `# TODO: não implementado` explícito para distinguir de mensagens esquecidas.

---

### B3 — Respawn tile hardcoded em world_server.py

**Arquivo:** [server/world_server.py](server/world_server.py) linha 704  
```python
RESPAWN_TILE = (115, 389)   # deve coincidir com spawn do mapa offline
```
O tile de respawn é hardcoded como constante de classe. Com múltiplas zonas/mapas (que o protocolo já prevê com `zone_change`), cada zona precisa de seu próprio respawn point.

**Fix:** Armazenar no `map_1_entities.json` ou como propriedade do `SpawnZone` do player.

---

### B4 — `systems.py` importa `from fonts import make as _font` no topo

**Arquivo:** [systems.py](systems.py) linha 7  
`systems.py` é importado pelo servidor headless (via `from systems import EnemyAISystem`). O import de `fonts` carrega Pygame font system. Funciona porque `SDL_VIDEODRIVER=dummy`, mas é uma dependência desnecessária no path do servidor.

**Fix:** Mover imports de Pygame (fonts, sounds, FLT, PROC, WARN) para uma subclasse `ClientSystem` ou usar lazy import dentro dos métodos `render()`.

---

### B5 — `get_players_in_aoi` usa Chebyshev mas `_collect_player_effects` usa linear scan

Métodos de AOI usam diferentes estratégias. Não há tipo inconsistência crítica mas dificulta entender qual é a métrica canônica. Centralizar em `def _in_aoi(cx, cy, ex, ey, radius)` em `shared/constants.py` ou `utils.py`.

---

## Resumo por categoria

| Categoria | # | Exemplos principais |
|-----------|---|---------------------|
| 🔴 Segurança/Bug ativo | 5 | move_player sem walkability, client_max_hp exploit, gold client-auth |
| 🟡 Performance/Duplicação | 9 | N² AOI sweep, CombatStateSystem duplicado, busy-wait tick loop |
| 🔵 Débito técnico | 11 | ServerSFX inner class, last_outcome singleton, factory functions mortas |
| 🟢 Limpeza | 5 | imports inline, respawn tile hardcoded, message types não implementados |

---

## Prioridade de correção

### Imediato (segurança)
1. **C1** — Implementar walkability check em `move_player`
2. **C2** — Remover `client_max_hp`/`client_ap` trust para novos chars
3. **C3** — Gold exclusivamente server-autoritativo
4. **C5** — Migrar `_pending_inv` para dict próprio

### Próximo sprint (performance)
5. **A1** — Mover CombatStateSystem para `core_systems.py`
6. **A2** — Spatial hash para AOI sweep
7. **A3** — Fix busy-wait do tick loop
8. **A8/A9** — Reverse map de players, mob aggro tracking

### Backlog técnico
9. **A4** — Extrair `_ServerSFX` para arquivo próprio
10. **A5/A6** — Caches de item lookup
11. **C4** — `deal_damage` retornar outcome em vez de singleton
12. **A7** — Quebrar `_tick()` em submétodos

---

*Issues de arquitetura pre-existentes estão documentadas em `arquitetura/PROBLEMAS_ARQUITETURA.md`.*
