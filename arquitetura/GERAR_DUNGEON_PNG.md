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
`maps/<nome>_dungeon_terrain.csv` + `maps/<nome>_dungeon_objects.csv`
(vazio) + `maps/<nome>_dungeon_entities.json`.

**NUNCA gera um `maps/<nome>.csv` único** (10/10/2026, bug real corrigido,
ver PROBLEMAS_ARQUITETURA.md §265) — `engine/map_loader.py::load_map_csv()`
prioriza SEMPRE o par `_terrain.csv`+`_objects.csv` sobre um arquivo único,
se os dois existirem. Gerar só o único era invisível/nunca lido de verdade
sempre que esse par já existisse por qualquer motivo (ex: dungeon criada
manualmente antes de adotar este script) — sem erro nenhum, o jogo
simplesmente carregava o par antigo por baixo dos panos.

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

## Re-regenerar uma dungeon JÁ EM PRODUÇÃO (10/10/2026) — NUNCA rodar o
## script direto por cima do `_entities.json` real

Depois que uma dungeon já tem o `_entities.json` editado à mão (nome de
boss, loot, gold, destino da transição de saída — a edição obrigatória
do item anterior), **rodar `convert()`/`png_to_dungeon.py` direto por
cima do arquivo real APAGA tudo isso** — `convert_dungeon_entities()`
sempre escreve zonas com stub (`"name": "EDIT_ME"`, `"loot": {}`), sem
exceção, mesmo que só o TERRENO tenha mudado (ex: autor só adicionou
borda no `_map.png`, sem tocar no `_entities.png` de propósito).

Pior ainda: se o PNG mudou de TAMANHO (borda adicionada = canvas
cresceu), as coordenadas de toda entidade existente **também mudam** —
não dá pra só "regenerar o terreno e deixar o JSON antigo intacto",
porque os bosses/elites ficariam nas coordenadas ANTIGAS, fora de
lugar no mapa NOVO (ou dentro de parede).

**Fluxo seguro pra esse caso** (usado de verdade na dungeon
Aracnópolis, bordas adicionadas ao redor do mapa inteiro):
1. `convert_dungeon_tiles()` do terreno pro `_terrain.csv` REAL (nunca
   um `<nome>.csv` único — ver §265 logo acima: o loader prioriza o
   par `_terrain.csv`/`_objects.csv` sobre o arquivo único, então
   escrever no arquivo errado fica SILENCIOSAMENTE ignorado, sem erro
   nenhum) — sempre seguro, só lê o `_map.png`, nunca toca em entidade.
   `_objects.csv` preenchido vazio (mesmo tamanho do terreno novo) se
   a dungeon nunca teve objeto nenhum nessa camada — se já tiver objeto
   de verdade colocado à mão, ele também precisa do MESMO deslocamento
   do passo 4 abaixo (não coberto automaticamente por este fluxo).
2. `convert_dungeon_entities()` do PNG de entidades pra um JSON
   **temporário** (nunca o caminho real) — dá as coordenadas NOVAS e
   corretas, mas com nome/loot stub.
3. Carrega o `_entities.json` REAL (antigo, com todo o conteúdo já
   escrito à mão) e o JSON temporário (coordenadas novas).
4. Casa cada zona ANTIGA com a zona NOVA mais próxima usando
   **correspondência ótima bipartida** (`scipy.optimize.
   linear_sum_assignment`, minimiza a soma total de distância de TODOS
   os pares ao mesmo tempo) — NUNCA correspondência gulosa (pegar a
   mais próxima zona por zona, em ordem de lista): gulosa pode "roubar"
   o par certo de uma zona processada depois só por ordem, gerando
   casamentos errados de 30+ tiles de distância (achado real rodando
   isso pela 1ª vez — a versão gulosa produziu 4 casamentos visivelmente
   errados; a ótima produziu 41/41 corretos, todos a 4-6 tiles de
   distância, consistente com o deslocamento real da borda).
5. Pra cada zona casada: copia as coordenadas NOVAS por cima da zona
   ANTIGA (que mantém nome/loot/gold/level/race/class intactos). Mesma
   lógica pro `"player"` (só coordenada, sem customização — usa a nova
   direto) e pra `"transitions"` (usa `x`/`y` novos — posição da saída
   DENTRO da dungeon — mas preserva `target_map`/`target_x`/`target_y`,
   que é a configuração real já corrigida, nunca o placeholder).
6. Valida (JSON parse + `request_dungeon_enter` de teste real, ver
   `tests/helpers.py`) ANTES de considerar pronto — **confira as
   dimensões REPORTADAS pelo `Tilemap` carregado** (`ws._map_bundles[...]`,
   `tm.map_width_tiles`/`map_height_tiles`), nunca só `wc -l` no arquivo
   em disco: o arquivo certo pode estar correto e mesmo assim nunca
   ser o que o jogo carrega de verdade (§265 — exatamente o que
   aconteceu na 1ª tentativa desta mesma regeneração).

Script usado (não commitado — é uma ferramenta de uma vez, não parte
do pipeline permanente; se uma 2ª dungeon precisar do mesmo
retrabalho, vale promover isso pra dentro de
`tools/png_to_dungeon.py` como uma flag `--merge-existing`, em vez de
reescrever o script toda vez).
