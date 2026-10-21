# Catálogo do atlas de UI — `assets/ui/ui_elements.png`

Referência de cada elemento do atlas desenhado pelo usuário. Código: `ui/ui_atlas.py`
(ponto único — as constantes de lá são a fonte de verdade das coordenadas; este
arquivo explica o que cada uma É e PARA QUE serve). Mockup de uso:
`assets/content/exemplo.png`. Montagem/interação de janela (centralizar na área
segura, botão no soltar, campo, seletor, barra de rolagem): `ui/atlas_window.py`
(`Frame`, `PressState`, `ScrollState`) — usado por TODA tela do atlas.

## Regras gerais

- O atlas é uma **grade de tiles 16×16** (256×256 = 16×16 tiles). Coordenadas abaixo
  são `(coluna, linha)` de tile.
- Janelas são montadas **como um mapa**: carimbando tiles inteiros. Cantos fixos,
  bordas e miolo **repetidos** — nunca esticados nem recortados por caixa de contorno.
- Composição em espaço RAW (1x) e ampliação 1x só no fim, por `ui_atlas.SCALE`
  (mesmo SCALE dos nameplates, `ui/hud_bars.py`) — nunca a escala de UI do jogador.
- Larguras com texto (botão, campo, seletor, janela) são calculadas a partir do texto
  medido com a fonte de desenho — a fonte acompanha a escala de UI, o atlas não.
- **Área segura**: toda janela fica centralizada dentro de uma margem de 10% da altura
  da tela no topo e acima da barra de ações, e 10% da largura nas laterais
  (`atlas_window.safe_area`; no GameEngine, `MenuHandlers._mm_safe_area`). Lista que
  não cabe rola.
- Variante "pressionada" (quando existe) é o mesmo elemento na coluna/bloco ao lado,
  mais chapado. Botões agem no **soltar** do mouse.

## Painéis

| Elemento | Tiles | Constante | Uso |
|---|---|---|---|
| Painel de madeira | (0,8)–(3,11) | `_PANELS["wood"]` | Menus (pausa, som, resolução...). Cantos (0,8)/(3,8)/(0,11)/(3,11); bordas topo (2,8) [variante (1,8)], base (1,11) [(2,11)], esq. (0,10) [(0,9)], dir. (3,10) [(3,9)]; miolo liso (1,10). |
| Painel escuro | (6,0)–(11,3) | `_PANELS["dark"]` | Janelas de lista (atalhos, seleção de personagem, inventário). Cantos (6,0)/(11,0)/(6,3)/(11,3); topo (7,0); base (8,3) [variante (7,3)]; esq. (6,1); dir. (11,1); miolo (7,1). |
| Janela de pergaminho | (0,0)–(5,7) + rodapé escuro | `_QUEST`, `quest_window_raw` | Diálogo de quest (mockup, janela 1). Linha 0 = barra de título: (0,0) esq., (2,0) puxador na 2ª coluna, (1,0) repetido, X `QUEST_CLOSE` (5,0) na última. Pergaminho nas colunas 0..tw-2: cantos (1,1)/(4,1)/(1,6)/(4,6); topo (2,1) [variante (3,1)]; base (3,6) [(2,6)]; esq. (1,2..5) e depois (1,3) repetido; dir. (4,2..5) e depois (4,3); miolo liso (2,4) com manchas (2,2)/(3,2)/(2,3)/(3,4)/(2,5) espalhadas. Coluna da direita = cromado (5,1) com a barra de rolagem por cima. Rodapé (2 linhas, botões "altos"): (6,1)/(6,3) esq., (7,1)/(8,3) meio, (5,1)/(11,3) dir. |

## Molduras e campos

| Elemento | Tiles | Constante | Uso |
|---|---|---|---|
| Moldura vazada | (6,4)–(8,6) | `_FRAME`, `blit_frame` | Agrupar linhas (seções da lista de atalhos, linhas da seleção de personagem). Cantos e bordas; miolo transparente. Mín. 2×2. |
| Campo de texto | (9,4) esq., (10,4) meio, (11,4) dir. | `blit_text_field`, `FIELD_ART_Y` | **Qualquer campo onde o jogador digita ou define um valor** (tecla de atalho, nome...). 1 linha; meio repetível. Arte em y 2..13 do tile. |
| Slot claro | (9,5) | `SLOT` | Slot de item com ícone por cima (recompensas do diálogo de quest; equipamento/inventário a confirmar). Ícone desenhado em resolução de tela dentro de 1px de borda. |
| Slot marrom | (10,5) | — (pendente) | Célula da grade do inventário (a confirmar). |
| Setinhas de paginação | (9,6) | — (pendente) | ◂ ▸ pequenas (criação de personagem / inventário, a confirmar). |

## Botões

| Elemento | Tiles | Constante | Uso |
|---|---|---|---|
| Botão "baixo" (1 linha) | idle (6..8, 9) · pressionado (9..11, 9) | `blit_button(tall=False)` | Botões em lista empilhada (menu principal), linhas de lista. Arte em y 1..15. |
| Botão "alto" (2 linhas) | idle (6..8, 10..11) · pressionado (9..11, 10..11) | `blit_button(tall=True)` | Botão solto entre linhas (Voltar). Arte em y 10..24 do bloco de 32px. |

Ponta esquerda / meio (repetível) / ponta direita nas 3 colunas de cada bloco.

## Setas, slider e rolagem

| Elemento | Tiles (idle · pressionado) | Constante | Uso |
|---|---|---|---|
| Seta ▲ | (12,0) · (13,0) | `SCROLL_UP` | Topo da barra de rolagem. |
| Seta ▼ | (12,1) · (13,1) | `SCROLL_DOWN` | Base da barra de rolagem. |
| Seta ◀ | (12,2) · (13,2) | `ARROW_LEFT` | Slider (diminuir), seletor ◀ valor ▶. |
| Seta ▶ | (12,3) · (13,3) | `ARROW_RIGHT` | Slider (aumentar), seletor. |
| Trilho horizontal | (13,4) | `SLIDER_TRACK` | Trilho do slider de volume (repetido entre as setas). |
| Barrinha de nível | (14,4) | `SLIDER_FILL_BAR` | 3px de largura; repetida colada a cada 3px por cima do trilho, quantidade proporcional ao valor. |
| Trilho vertical | (12,5) | `SCROLL_TRACK` | Barra de rolagem (repetido entre ▲ e ▼). (12,4) é uma variante mais fina, não usada. |
| Puxador | (12,6) · (13,6) | `SCROLL_GRIP` | Posição da rolagem; arrastável. Posicionado em pixel livre. |

Barra de rolagem montada e interativa: `atlas_window.Frame.scrollbar` (estado em
`ScrollState`) — `always=True` desenha parada mesmo sem nada pra rolar (diálogo de
quest, como no mockup).

## Ícones

| Elemento | Tiles (ligado · desligado) | Constante | Uso |
|---|---|---|---|
| Alto-falante | (12,7) · (13,7) | `ICON_SFX_ON/OFF` | Volume de efeitos (desligado também em 0%). |
| Nota musical | (12,8) · (13,8) | `ICON_MUSIC_ON/OFF` | Volume de música. |

## Histórico

Ver `arquitetura/HISTORICO_FEATURES.md` §65 (atlas + tela de som), §66 (menu de pausa),
§67 (modal de atalhos do teclado + campo de texto) e §68 (diálogo de quest + módulo
`ui/atlas_window.py`).
