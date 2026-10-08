# Gerar mapa de dungeon a partir de PNG

> Pipeline específico pra dungeons instanciadas (07/10/2026). Separado de
> `tools/png_to_map.py` (conversor geral de mapas do mundo aberto) —
> não modifica esse arquivo nem seu comportamento.

## Por que um script separado

Dungeons têm 3 convenções próprias que o conversor geral não tem:
- **Terreno binário** (só branco/preto, sem paleta de cores variada),
  com estética de CAVERNA (chão `"_"`, parede `"c"`) em vez de mountain
  border/chão de pedra de cidade.
- **Vermelho clusterizado por TAMANHO** — o conversor geral trata cada
  pixel como 1 entidade isolada; aqui, o TAMANHO do grupo de pixels
  vermelhos adjacentes decide se é um mob elite ou um boss.
- **Zonas sem respawn** (`respawnable: false`) e **loot de boss inline
  no JSON** (`loot`/`gold_min`/`gold_max`) — campos que o conversor
  geral não gera.

## Convenção de cores

### `<nome>_map.png` (terreno)

| Cor | Hex | Resultado |
|---|---|---|
| Branco `(255,255,255)` | `#FFFFFF` | chão (`"_"`, chão genérico — mesma estética de caverna) |
| Preto `(0,0,0)` | `#000000` | parede (`"c"`, parede de caverna) |

Qualquer outra cor é classificada pelo mais próximo (distância RGB) —
pensado pra ser estritamente 2 cores, não uma paleta.

### `<nome>_entities.png` (entidades)

| Cor | Hex | Resultado |
|---|---|---|
| Vermelho `(255,0,0)`, cluster de **1 pixel** | `#FF0000` | zona de spawn de mob **elite**, sem respawn (`radius=1, count=1`) |
| Vermelho `(255,0,0)`, cluster de **4 pixels** (bloco 2×2) | `#FF0000` | zona de spawn de **boss**, sem respawn, com stub de loot vazio pro autor preencher |
| Amarelo `(251,242,54)`, o de **menor Y** (mais ao norte) | `#FBF236` | transição de **SAÍDA** (placeholder: `maps/map_1.csv` em `(0,0)` — EDITE) |
| Amarelo `(251,242,54)`, o de **maior Y** (mais ao sul) | `#FBF236` | ponto de **entrada/respawn** (`"player"` no JSON) |
| Preto `(0,0,0)` | — | vazio, ignorado |

Clusters vermelhos de tamanho diferente de 1 ou 4 pixels geram um
**aviso** no terminal e são pulados — ajuste o PNG (pixels soltos
demais ou blocos maiores que 2×2 não têm convenção definida ainda).

O script espera exatamente 2 pixels amarelos. Se achar outra
quantidade, avisa e deixa `"player"`/`"transitions"` pra você editar
na mão.

## Uso

```bash
py tools/png_to_dungeon.py assets/maps/<nome>_dungeon \
    [--race Aranha] [--class Guerreiro] \
    [--elite-level-min 5] [--elite-level-max 7] [--boss-level 7]
```

Lê `assets/maps/<nome>_dungeon_map.png` +
`assets/maps/<nome>_dungeon_entities.png`, gera
`maps/<nome>_dungeon.csv` + `maps/<nome>_dungeon_entities.json`.

## Edição manual obrigatória depois de gerar

O JSON gerado **não está pronto pra jogo** — sempre precisa de 2 ajustes:

1. **Cada zona de boss** (`spawns[0].tier == "boss"`) tem `"loot": {}`
   vazio — preencha com `{item_id: chance}` (ex: `{"iron_sword": 0.3}`,
   chance EXATA, sem multiplicador de tier) e `gold_min`/`gold_max`.
2. **A transição de saída** (única entrada de `"transitions"`) aponta
   pro placeholder `maps/map_1.csv` em `(0,0)` — troque
   `target_map`/`target_x`/`target_y` pela entrada real da dungeon no
   mapa aberto.
3. Também é responsabilidade sua adicionar a transição de ENTRADA no
   `_entities.json` do mapa ABERTO (ex: `map_1_entities.json`) com o
   campo opcional `"dungeon_key"` apontando pra entrada certa de
   `content/dungeon_definitions.py::DUNGEON_TABLE` — ver CLAUDE.md
   ("Transição que leva a uma instância").

## Ponto único de verdade

Qualquer dungeon FUTURA usa o MESMO script — nunca escrever um
conversor novo por dungeon. Ajustar a convenção de cores (ex: um 2º
tipo de mob) é editar `tools/png_to_dungeon.py` uma vez só, não
duplicar lógica.
