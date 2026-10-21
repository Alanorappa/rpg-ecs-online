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
| Painel escuro | (6,0)–(11,3) | `_PANELS["dark"]` (+ `dark_list_window_raw`: coluna da direita cromada (5,1) pra barra de rolagem, seleção de personagem) | Janelas de lista (atalhos, seleção de personagem, inventário). Cantos (6,0)/(11,0)/(6,3)/(11,3); topo (7,0); base (8,3) [variante (7,3)]; esq. (6,1); dir. (11,1); miolo (7,1). |
| Janela escura com barra de título | linha 0 da janela de pergaminho + corpo escuro | `_TITLED_DARK`, `titled_dark_window_raw` | Inventário (mockup, janela 2). Barra cinza com X (5,0) na última coluna e puxadores (2,0) na 3ª e na 5ª-a-partir-do-fim; laterais (0,1)/(5,1); base (0,7)/(8,3)/(11,3); miolo (7,1). X: `Frame.close_button`. |
| Janela de pergaminho | (0,0)–(5,7) + rodapé escuro | `_QUEST`, `quest_window_raw` | Diálogo de quest (mockup, janela 1). Linha 0 = barra de título: (0,0) esq., (2,0) puxador na 2ª coluna, (1,0) repetido, X `QUEST_CLOSE` (5,0) na última. Pergaminho nas colunas 0..tw-2: cantos (1,1)/(4,1)/(1,6)/(4,6); topo (2,1) [variante (3,1)]; base (3,6) [(2,6)]; esq. (1,2..5) e depois (1,3) repetido; dir. (4,2..5) e depois (4,3); miolo liso (2,4) com manchas (2,2)/(3,2)/(2,3)/(3,4)/(2,5) espalhadas. Coluna da direita = cromado (5,1) com a barra de rolagem por cima. Rodapé (2 linhas, botões "altos"): (6,1)/(6,3) esq., (7,1)/(8,3) meio, (5,1)/(11,3) dir. |

## Molduras e campos

| Elemento | Tiles | Constante | Uso |
|---|---|---|---|
| Moldura vazada | (6,4)–(8,6) | `_FRAME`, `blit_frame` | Agrupar linhas (seções da lista de atalhos, linhas da seleção de personagem) e marcar o item ESCOLHIDO (card de classe na criação de personagem — só o escolhido tem moldura). Cantos e bordas; miolo transparente; contorno ~8px pra dentro do tile. Mín. 2×2. |
| Campo de texto | (9,4) esq., (10,4) meio, (11,4) dir. | `blit_text_field`, `FIELD_ART_Y` | **Qualquer campo onde o jogador digita ou define um valor** (tecla de atalho, nome...). 1 linha; meio repetível. Arte em y 2..13 do tile. |
| Slot claro | (9,5) | `SLOT` | Slot de equipamento / recompensa. Ícone em resolução de TELA (`aw.slot_icon_px()`) dentro de 1px de borda. `Frame.slot(...)`. |
| Slot marrom | (10,5) | `SLOT_DARK` | Célula de grade de itens (bolsa, loja, troca). `Frame.grid(...)`. |
| Linha divisória | — (1px, cor da moldura (86,84,82)) | `DIVIDER_COL`, `Frame.divider_h/v` | Separar áreas de uma janela (inventário do mockup: equipamento | bolsa). |
| Checkbox | (12,9) desmarcado · (13,9) marcado | `CHECKBOX_OFF/ON`, `Frame.checkbox` | Opção liga/desliga com rótulo à direita (clicável junto). |
| Aba | ativa (0..2, 12) · inativa (3..5, 12) | `blit_tab`, `Frame.tabs` | Trocar o conteúdo da janela. 1 linha, aberta embaixo; ponta esq./meio repetível/ponta dir.; largura pelo texto. Fica DENTRO do painel, encostada na borda de cima (decisão do usuário). Ativa = cor do miolo do painel escuro. |
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
`ScrollState`) — só aparece quando há o que rolar (decisão do usuário); `always=True`
existe pra desenhá-la parada mesmo sem rolagem, se algum dia uma tela pedir.

## Ícones

| Elemento | Tiles (ligado · desligado) | Constante | Uso |
|---|---|---|---|
| Alto-falante | (12,7) · (13,7) | `ICON_SFX_ON/OFF` | Volume de efeitos (desligado também em 0%). |
| Nota musical | (12,8) · (13,8) | `ICON_MUSIC_ON/OFF` | Volume de música. |

## Histórico

Ver `arquitetura/HISTORICO_FEATURES.md` §65 (atlas + tela de som), §66 (menu de pausa),
§67 (modal de atalhos do teclado + campo de texto), §68 (diálogo de quest + módulo
`ui/atlas_window.py`), §69 (seleção de personagem), §70 (criação de personagem) e §71 (kit de peças).

## Peças compostas do kit (`ui/atlas_window.py`)

| Peça | Uso |
|---|---|
| `prompt(...)` | Aviso com mensagem e botões altos (convites de troca/duelo/grupo, menu do treinador). |
| `quantity_window` + `quantity_state`/`quantity_key` | Modal "quantos?" (loja, troca, correio): ◀ quantidade ▶, digitação, roda do mouse, Cancelar/Confirmar. |
| `item_icon(item, font)` | Ícone do item; sem ícone no catálogo → quadrado da cor da raridade com a inicial. |
| `Frame.rreleased` / `right_click(...)` | Clique DIREITO no soltar (equipar, comprar, vender, ofertar...). |
| `Frame(press=None)` | Janela só de FUNDO (por baixo de um modal) — nunca compartilhar o PressState do modal. |
| `text_field(placeholder=, align=)`, `field_hover` | Campo com texto-guia cinza e alinhado à esquerda; detectar clique fora pra desfocar. |
