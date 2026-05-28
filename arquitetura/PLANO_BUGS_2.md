# Plano de Correção de Bugs — Sessão 2

> Registrado em 2026-05-27. Reportados pelo usuário após Fase 3 do PLANO_ACAO.md.

---

## Legenda
- 🔴 Crítico — quebra gameplay
- 🟡 Moderado — comportamento errado mas contornável
- 🟢 Cosmético / UX

---

## Bug 1 — Player ocupa tile de mob (WASD / clique) 🔴

**Causa:** `TileValidationSystem.is_tile_walkable` (systems.py:427-429) tem exceção
incondicional: qualquer jogador pode andar para o tile de qualquer inimigo.
Isso foi adicionado para evitar race conditions durante perseguição, mas permite
que WASD mova o player para dentro do mob.

**Arquivos:** `systems.py`

**Fix:** Tornar a exceção condicional:
- Só permitir andar para o tile do inimigo se `occupant_id == ignore_eid`
  (ou seja, somente quando o mob é o alvo explícito de perseguição).
- WASD passa `ignore_eid=-1` → tile de mob bloqueado.
- `_auto_move_step` já passa `ignore_eid=target_eid` (linha 1745) → continua funcionando.

**Status:** ✅ 0906ef0

---

## Bug 2 — Mobs dividem o mesmo tile 🔴

**Causa:** EnemyAISystem não verifica tiles ocupados por OUTROS mobs ao escolher
o próximo passo. `TileValidationSystem._occupied` inclui todos os mobs, mas ao
calcular o path, os mobs não são adicionados como `dynamic_obstacles`.

**Arquivos:** `systems.py` (EnemyAISystem)

**Fix:** No método de movimento de mob (EnemyAISystem), antes de mover para um tile,
verificar se já está ocupado por outro mob. Usar o cache `_occupied` do
TileValidationSystem ou adicionar mobs vizinhos como dynamic_obstacles no A*.

**Status:** ✅ 0906ef0

---

## Bug 3 — Player consegue selecionar mobs na fog 🟡

**Causa:** Suspeita que a `Visible` tag é adicionada ao spawnar mob (online) antes de
FogSystem processar o LOS. No tick de spawn, o mob tem `Visible` e pode ser clicado.
Na prática, FogSystem remove `Visible` no próximo frame para mobs fora de LOS — mas
há uma janela de 1 frame onde o targeting acontece.
Também: `is_on_screen()` não é suficiente para filtrar fog.

**Arquivos:** `systems.py` (MouseTargetingSystem), `game.py`

**Fix:** Em `_enemy_at_world_pos` e `_space_engage`, além de `Visible`, verificar que
o tile do mob está em `fog.visible`. Ou: adicionar componente `InFog` diferenciado.

**Status:** ✅ 0906ef0

---

## Bug 4 — Minimap mostra mobs na fog 🟡

**Causa:** A lista `_enemy_tiles` (game.py:1467-1469) filtra por `Visible`, porém
em modo online `Visible` pode ser adicionado pelo servidor (AOI) sem o LOS local
ser cheio. Mobs em AOI mas atrás de paredes mantêm `Visible` e aparecem no minimap.

**Arquivos:** `game.py`, `minimap.py`

**Fix:** Ao construir `_enemy_tiles`, cruzar com `fog.visible` (set de tiles visíveis):
```python
_enemy_tiles = [
    (etm.current_tile_x, etm.current_tile_y)
    for _, _, _, etm in self.world.get_entities_with(Enemy, Visible, TileMovement)
    if (etm.current_tile_x, etm.current_tile_y) in _fog_mm.visible
]
```

**Status:** ✅ 0906ef0

---

## Bug 5 — WASD não cancela perseguição de mob 🟡

**Causa:** `PlayerInputSystem.update` (systems.py:1394-1402): ao pressionar WASD,
cancela `auto_move` mas mantém `combat_state.is_pursuing = True`. O movimento
manual deveria parar a perseguição; pressionar ESPAÇO re-engaja.

**Arquivos:** `systems.py` (PlayerInputSystem.update)

**Fix:** No bloco WASD (linhas ~1396-1402), além de limpar `auto_move`, também
setar `combat_state.is_pursuing = False`. O alvo (`target_entity_id`) permanece
selecionado — pressionar ESPAÇO re-inicia a perseguição.

**Status:** ✅ 0906ef0

---

## Bug 6 — Skill sem alvo selecionado não auto-seleciona mob adjacente 🟡

**Causa:** `_resolve_target` (systems.py:5852) seleciona o mob visível mais próximo
na tela. Mas não prioriza HP baixo quando múltiplos mobs estão equidistantes, e
pode selecionar um mob fora do range da skill mesmo havendo um adjacente.

**Arquivos:** `systems.py` (SkillSystem._resolve_target)

**Fix:** Ajustar `_resolve_target` para, quando `_max_range > 0`, priorizar mobs
dentro do range da skill, com desempate por HP (menor HP = maior prioridade).

**Status:** ✅ 0906ef0

---

## Bug 7 — Skills de área exigem alvo selecionado 🟡

**Causa:** Skills como Impacto, Brado Provocativo, Fatiador de Corpos têm seus
handlers configurados para retornar False quando não há alvo. Mas por serem AoE,
não dependem de alvo específico.

**Arquivos:** `skill_config.py`, `skill_handlers.py`

**Fix:** Adicionar `"needs_target": False` (ou similar) às entradas AoE do
SKILL_CATALOG. No despacho de skill, se `needs_target=False`, pular a resolução
de alvo e chamar o handler diretamente.

**Status:** ✅ 0906ef0

---

## Bug 8 — Player respawna com efeitos ativos (DoT) 🔴

**Causa:** Lado servidor: `_handle_player_death` (respawn_system.py) não limpa
`StatusEffects` nem `ActiveRegen` do player. O servidor continua aplicando ticks
de DoT após o respawn.

**Arquivos:** `server/respawn_system.py`

**Fix:** Em `_handle_player_death`, limpar `StatusEffects.effects` e remover
`ActiveRegen` do player.

**Status:** ✅ 0906ef0

---

## Bug 9 — Consumível com 0 stacks some da action bar 🟢

**Causa:** Ao consumir o último item (`item.stack <= 0`), `inv.items.remove(item)`
remove o objeto da lista mas `cbar.slots[i]` mantém o nome. O slot deveria aparecer
escurecido. Possível causa: em algum path de persistência/reload online, `cbar.slots`
é limpo quando o item não existe no inventário.

**Arquivos:** `game.py` (_draw_consumable_bar), `systems.py` (ConsumableSystem)

**Nota:** O código em `_draw_consumable_bar` (game.py:5278-5291) JÁ implementa
o overlay escuro quando `item` é None. Verificar se há path que limpa `cbar.slots[i]`
ao receber SAVE_STATE ou LOOT_RESULT do servidor.

**Status:** ✅ 0906ef0

---

## Bug 10 — Interceptar dessincroniza posição (corrige só ao andar) 🟡

**Causa:** Ao receber `ENTITY_MOVE` para o próprio player como correção de posição
(game.py:3107-3118), o código atualiza `TileMovement.current_tile_x/y` mas NÃO
atualiza `Position.x/y`. O pixel position fica "preso" na posição visual do dash
até o próximo movimento, que força o TileMovementSystem a recalcular.

**Arquivos:** `game.py` (ENTITY_MOVE handler)

**Fix:** Após atualizar `current_tile_x/y`, também atualizar `Position.x/y` para
o centro do tile:
```python
_player_pos.x = real_tx * TILE_SIZE + TILE_SIZE / 2
_player_pos.y = real_ty * TILE_SIZE + TILE_SIZE / 2
```

**Status:** ✅ 0906ef0

---

## Ordem de correção sugerida

| Prioridade | Bug | Justificativa |
|-----------|-----|--------------|
| 1 | B8 — Respawn com efeitos | Servidor — seguro, isolado |
| 2 | B10 — Interceptar desync | Isolado, 3 linhas |
| 3 | B1 — Player entra no tile | Core collision — muitos paths |
| 4 | B5 — WASD cancela pursuit | Gameplay feel, 2 linhas |
| 5 | B4 — Minimap fog | 1 linha na query |
| 6 | B6 — Auto-target HP | UX de skill |
| 7 | B7 — AoE sem alvo | Requer skill_config |
| 8 | B2 — Mobs no mesmo tile | AI pathfinding, mais risco |
| 9 | B3 — Target fog | Depende de B4 |
| 10 | B9 — Consumível some | Verificar primeiro se já está correto |
