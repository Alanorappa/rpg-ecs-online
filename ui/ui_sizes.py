"""ui_sizes.py — Único lugar onde TODOS os tamanhos "de design" (escala 1.0)
da UI são declarados: painéis modais, tooltip, HUD, hotbar, minimapa, fontes.

Antes dessa rodada, cada um desses valores vivia espalhado como constante de
classe/módulo dentro do próprio arquivo do painel (ex: `_PANEL_W = 720` em
client/inventory_handlers.py), sem nenhum lugar único pra ajustar a UI como
um todo — ver arquitetura/PROBLEMAS_ARQUITETURA.md item IU4.

Cada arquivo de painel continua com sua constante local de mesmo nome
(`_PANEL_W`, `PANEL_W` etc. — não renomeada, pra não precisar tocar nos
~400 usos de `self._u(_PANEL_W)` espalhados por aqueles arquivos), só que
agora ela apenas REDIRECIONA pra aqui (`_PANEL_W = UI.INVENTORY_W`). Editar
um valor aqui afeta o painel correspondente automaticamente.

Todos os valores são pixels "base" (o que você desenharia em ui_scale=1.0).
Eles passam por self._u()/self._set_panel_scale() no arquivo de origem, e
escalam proporcionalmente junto com a "Escala da UI" do menu de pausa — mudar
um número aqui não muda esse comportamento, só o tamanho de referência.

Sobre POSIÇÃO (não só tamanho): todo painel modal listado abaixo é
CENTRALIZADO — na tela inteira (`_panel_origin`/`self._u(...)` direto) ou na
"área livre" que exclui HUD/minimapa (`_safe_panel_origin`, ver
GameEngine/UIScaleMixin) — e a partir desse centro, soma um
`<PAINEL>_OFFSET_X`/`<PAINEL>_OFFSET_Y` configurável (seção "Offsets de
posição" mais abaixo). Mude o offset de um painel pra deslocá-lo sem editar
nenhum outro arquivo; (0, 0) = sempre centralizado, igual era antes desses
offsets existirem. Exceção: `LootSystem` (janela de loot) não é
centralizada — ela é ANCORADA perto do corpo clicado, então não tem offset
configurável aqui (ver comentário na seção "Loot" mais abaixo).
"""


class UI:
    # ── Painéis modais — largura/altura de design (escala 1.0) ────────────
    # Cada par É o tamanho do retângulo de fundo do painel inteiro (moldura +
    # conteúdo). Aumentar deixa o painel maior; a posição continua
    # centralizada automaticamente (ver nota de POSIÇÃO no topo do arquivo).

    INVENTORY_W, INVENTORY_H     = 660, 580   # painel de Equipamentos/Inventário (tecla I)
    DEBUG_W, DEBUG_H             = 820, 620   # modal de debug (F12)
    HABILIDADES_W, HABILIDADES_H = 720, 560   # painel de Habilidades (tecla H)
    HOTBAR_EDITOR_W              = 560        # editor de atalhos (tecla K) — altura é dinâmica (depende do nº de slots/linhas)
    CRAFTING_W, CRAFTING_H       = 700, 510   # modal de Forja/Reciclagem (ferreiro)
    TRAINER_W, TRAINER_H         = 640, 480   # modal de treinamento de skills (trainer NPC)
    SHOP_W, SHOP_H                = 1120, 700  # modal de loja (comerciante NPC) — o maior painel do jogo
    SHOP_QTY_MODAL_W, SHOP_QTY_MODAL_H = 460, 240  # mini-modal "quantidade" que abre por cima da loja
    LOOT_MODAL_W                  = 260        # janela de loot (clique direito em corpo) — altura é dinâmica (TITLE_H + nº de linhas)
    QUEST_DIALOG_W, QUEST_DIALOG_H     = 480, 400  # diálogo de aceitar/entregar quest (NPC com !/?)
    QUEST_JOURNAL_W, QUEST_JOURNAL_H   = 700, 520  # diário de quests (tecla J)
    TALENTS_W, TALENTS_H          = 800, 640   # árvore de talentos (tecla T)
    SKILL_LEVELS_W, SKILL_LEVELS_H = 700, 560  # painel de Skill Level (tecla L) — nível+xp do personagem + 11 trilhas, read-only
    TRADE_W, TRADE_H              = 440, 450   # janela de trade (player↔player) — bag própria + 2 colunas de oferta
    CHAT_W, CHAT_H                = 380, 220   # janela de chat (canto inferior esquerdo) — abas Local/Mundial/Combate
    TRADE_POPUP_W, TRADE_POPUP_H  = 140, 70    # mini-popup "Trade" (Shift+clique num player remoto)
    TRADE_INVITE_W, TRADE_INVITE_H = 360, 150  # modal "Fulano quer negociar"

    # Menu de pausa (ESC) e seus submenus — cada um é um painel separado
    MENU_QUIT_CONFIRM_W, MENU_QUIT_CONFIRM_H = 320, 150   # "Tem certeza que deseja sair?"
    MENU_MAIN_W                              = 260         # tela principal do menu — altura é dinâmica (depende do nº de botões)
    MENU_RESOLUTION_W, MENU_RESOLUTION_H     = 360, 220   # submenu "Resolution" (1x/1.25x/1.5x)
    MENU_INTERFACE_W, MENU_INTERFACE_H       = 340, 200   # submenu "Interface" (slider de Escala da UI)
    MENU_SOUND_W, MENU_SOUND_H               = 400, 230   # submenu "Sound" (volume música/efeitos)

    # ── Inventário — geometria INTERNA (dentro do painel acima) ────────────
    # Arquivo: client/inventory_handlers.py
    INVENTORY_PAD       = 10    # respiro entre a borda do painel e o conteúdo (todos os lados)
    INVENTORY_HEADER_H  = 28    # altura reservada pro título "Equipamentos" no topo
    INVENTORY_EQ_W      = 260   # largura da coluna da ESQUERDA (lista de slots de equipamento: Cabeça, Ombros...)
    INVENTORY_EQ_SLOT_H = 36    # altura de cada linha de slot de equipamento (ícone + nome do item)
    INVENTORY_EQ_ICON   = 28    # tamanho do ícone do item dentro de cada slot de equipamento
    INVENTORY_BODY_H    = 360   # altura total da área de conteúdo (lista de equipamento + grade da mochila)
    INVENTORY_SLOT      = 64    # tamanho (largura = altura) de cada slot quadrado da mochila
    INVENTORY_COLS      = 5     # quantos slots de mochila cabem por linha (não é pixel — é uma contagem)
    INVENTORY_GAP       = 4     # espaço entre slots vizinhos da grade da mochila

    # ── Editor de Hotbar (tecla K) — geometria interna ─────────────────────
    # Arquivo: client/hotbar_editor_handlers.py
    HOTBAR_EDITOR_SLOT_SZ = 52   # (não usado atualmente — reservado)
    HOTBAR_EDITOR_GAP     = 10   # (não usado atualmente — reservado)

    # ── Crafting (Forja/Reciclagem) — geometria interna ────────────────────
    # Arquivo: crafting_system.py
    CRAFTING_LEFT_W    = 290   # largura do painel ESQUERDO (a tela de reciclagem ou forja em si)
    CRAFTING_RIGHT_W   = 392   # largura do painel DIREITO (grade da mochila pra arrastar item/material)
    CRAFTING_DIVIDER   = 18    # espessura da linha divisória entre os painéis esquerdo e direito
    CRAFTING_PAD       = 14    # respiro entre a borda do painel e o conteúdo
    CRAFTING_SLOT_SZ   = 60    # tamanho de cada slot da grade de mochila (painel direito)
    CRAFTING_SLOT_GAP  = 6     # espaço entre slots vizinhos da grade de mochila

    # ── Trade (player↔player) — geometria interna ──────────────────────────
    # Arquivo: client/trade_handlers.py
    TRADE_PAD      = 14   # respiro entre a borda do painel e o conteúdo
    TRADE_SLOT_SZ  = 56   # tamanho de cada slot de oferta (5 por lado)
    TRADE_SLOT_GAP = 8    # espaço entre slots vizinhos de oferta

    # ── Chat (Local/Mundial/Combate) — geometria interna ───────────────────
    # Arquivo: client/chat_handlers.py
    CHAT_PAD       = 6    # respiro entre a borda da janela e o conteúdo
    CHAT_TAB_H     = 22   # altura da linha de abas
    CHAT_INPUT_H   = 22   # altura do campo de digitação (oculto na aba Combate)
    CHAT_LINE_H    = 16   # altura de cada linha de texto na área de mensagens
    CHAT_SCROLL_W  = 8    # largura da barra de rolagem — igual LOOT_SCROLL_W
    CRAFTING_BAG_COLS  = 5     # quantos slots cabem por linha na grade (contagem, não pixel)
    CRAFTING_ITEM_SLOT = 64    # tamanho do slot "item a reciclar" / "resultado da receita" (painel esquerdo)
    CRAFTING_MAT_SZ    = 46    # tamanho de cada slot de material exigido pela receita (painel esquerdo)

    # ── Treinador de skills — geometria interna ────────────────────────────
    # Arquivo: trainer_system.py
    TRAINER_PAD     = 16   # respiro entre a borda do painel e o conteúdo
    TRAINER_ROW_H   = 72   # altura de cada linha de skill na lista (ícone + nome + custo)
    TRAINER_ICON_SZ = 44   # tamanho do ícone da skill em cada linha

    # ── Loja (comerciante NPC) — geometria interna ──────────────────────────
    # Arquivo: systems.py (classe ShopSystem)
    SHOP_ROW_H         = 50    # altura de cada linha de item (à venda ou na mochila)
    SHOP_ICON_SZ       = 42    # tamanho do ícone do item em cada linha
    SHOP_MAX_ROWS      = 10    # quantas linhas ficam visíveis de uma vez antes de precisar rolar (contagem)
    SHOP_LEFT_W        = 530   # largura da coluna ESQUERDA (itens à venda pelo NPC)
    SHOP_RIGHT_W       = 530   # largura da coluna DIREITA (mochila do jogador, pra vender)
    SHOP_GAP           = 12    # espaço entre as duas colunas
    SHOP_BODY_Y_OFFSET = 126   # distância do topo do painel até a primeira linha de item (reserva título+abas)
    SHOP_FOOTER_H      = 62    # altura reservada no rodapé pro saldo de ouro + dica de uso

    # ── Loot (clique direito em corpo) — geometria interna ──────────────────
    # Arquivo: systems.py (classe LootSystem). Esse modal NÃO é centralizado
    # na tela — ele aparece ANCORADO perto do corpo clicado (com clamp pra
    # não passar da borda da tela). Ver nota de POSIÇÃO no topo do arquivo.
    LOOT_ROW_H    = 46   # altura de cada linha de item dentro do corpo
    LOOT_ICON_S   = 36   # tamanho do ícone do item em cada linha
    LOOT_TITLE_H  = 28   # altura da barra de título (nome do corpo + botão fechar)
    LOOT_MAX_ROWS = 5    # quantas linhas ficam visíveis antes de precisar rolar (contagem)
    LOOT_PAD      = 8    # respiro entre a borda do modal e o conteúdo
    LOOT_SCROLL_W = 8    # largura da barrinha de scroll quando há mais itens que LOOT_MAX_ROWS

    # ── Quests — geometria interna ───────────────────────────────────────────
    # Arquivo: quest_system.py (3 classes: tracker no HUD, diálogo, diário)
    QUEST_HUD_MAX_VISIBLE  = 3     # quantas quests aparecem ao mesmo tempo no tracker do HUD (contagem)
    QUEST_HUD_MARGIN_RIGHT = 10    # distância do tracker até a borda direita da tela
    # (sem QUEST_HUD_MARGIN_TOP aqui: a distância até o topo é CALCULADA em
    # quest_system.py::QuestSystem.HUD_MARGIN_TOP a partir de
    # MINIMAP_MARGIN_TOP+MINIMAP_SIZE — o tracker de quests fica sempre
    # colado embaixo do minimapa, então não faz sentido como número fixo
    # independente; editar MINIMAP_SIZE acima já ajusta os dois juntos)
    QUEST_DIALOG_PAD       = 16    # respiro do modal de diálogo de quest (aceitar/entregar)
    QUEST_JOURNAL_LIST_W   = 210   # largura da coluna de LISTA de quests dentro do diário (a outra parte é o detalhe da quest selecionada)
    QUEST_JOURNAL_PAD      = 14    # respiro do modal de diário de quests

    # ── Árvore de Talentos (tecla T) — geometria interna ────────────────────
    # Arquivo: talent_system.py
    TALENTS_NODE_W        = 64    # largura de cada "nó" (ícone de talento) na árvore
    TALENTS_NODE_H        = 64    # altura de cada nó
    TALENTS_NODE_COL_GAP  = 96    # distância centro-a-centro entre nós em colunas vizinhas (horizontal)
    TALENTS_NODE_ROW_GAP  = 90    # distância centro-a-centro entre nós em linhas vizinhas (vertical)
    TALENTS_GRID_ORIGIN_X = 60    # margem da grade de nós até a borda ESQUERDA do painel
    TALENTS_GRID_ORIGIN_Y = 70    # margem da grade de nós até o TOPO do painel
    TALENTS_TOOLTIP_W     = 320   # largura do tooltip que aparece ao passar o mouse num nó

    # ── Skill Level (tecla L) — geometria interna ──────────────────────────
    # Arquivo: skill_level_ui.py
    SKILL_LEVELS_PAD     = 20   # respiro entre a borda do painel e o conteúdo
    SKILL_LEVELS_HEADER_H = 56  # altura reservada pro título no topo
    SKILL_LEVELS_CHAR_ROW_H = 52  # altura da linha de Nível+XP do personagem (acima das 11 trilhas)
    SKILL_LEVELS_ROW_H   = 38   # altura de cada linha — 1 linha só (nome+level+bônus, barra com xp sobreposto)
    SKILL_LEVELS_BAR_W   = 230  # largura da barra de progresso de xp
    SKILL_LEVELS_BAR_H   = 22   # altura da barra — alta o bastante pro texto de xp ficar sobreposto, centrado
    SKILL_LEVELS_NAME_COL_W = 230  # largura reservada pro nome da skill antes da coluna "Lv X (+Y%)"

    # ── Mapa-múndi (overlay, tecla M) ────────────────────────────────────────
    # Arquivo: map_overlay.py. Esse painel NÃO usa pixel fixo — o tamanho é
    # sempre uma PROPORÇÃO da tela atual (0.70 = 70% da largura/altura da
    # janela, em qualquer resolução), por isso já nunca precisou do clamp de
    # "cabe na tela" que os outros painéis precisam.
    MAP_OVERLAY_W_RATIO = 0.70   # 70% da largura da tela
    MAP_OVERLAY_H_RATIO = 0.70   # 70% da altura da tela

    # ── Minimapa (canto superior direito, sempre visível) ───────────────────
    # Arquivo: minimap.py
    MINIMAP_SIZE         = 220   # tamanho (largura = altura) do quadrado do minimapa
    MINIMAP_RADIUS_TILES = 25    # quantos tiles ao redor do jogador o minimapa mostra — é uma CONTAGEM DE TILES, não pixel, por isso não muda com a Escala da UI
    MINIMAP_MARGIN_RIGHT = 10    # distância do minimapa até a borda direita da tela
    MINIMAP_MARGIN_TOP   = 68    # distância do minimapa até o topo da tela (abaixo do texto de zona/coordenadas)

    # ── Hotbar de skills (1-0) + barra de consumíveis, lado a lado ──────────
    # Arquivo: client/hotbar_handlers.py / client/consumable_bar_handlers.py.
    # As duas barras compartilham os mesmos 4 valores (slots do mesmo
    # tamanho nas duas), por isso não tem "HOTBAR_*" e "CONSUMABLE_*"
    # separados.
    HOTBAR_SLOT_W  = 68   # largura de cada slot de skill/consumível
    HOTBAR_SLOT_H  = 68   # altura de cada slot
    HOTBAR_ICON    = 64   # tamanho do ícone dentro do slot
    HOTBAR_PAD     = 6    # espaço entre slots vizinhos
    HOTBAR_ROW_GAP = 20   # espaço horizontal reservado ENTRE o fim da hotbar e o início da barra de consumíveis

    # ── HUD principal (barras de vida/mana/fúria/concentração/XP) ──────────
    # Arquivo: client/hud_handlers.py. Essas barras ainda não têm o clamp de
    # "respeita HUD/minimapa" que os modais já têm — ver PROBLEMAS_ARQUITETURA.md.
    HUD_BAR_W    = 200   # largura das barras de HP/mana/fúria/concentração/aljava (todas usam o mesmo valor)
    HUD_BAR_H    = 14    # altura dessas mesmas barras
    HUD_XP_BAR_H = 10    # altura da barra de XP (mais fina que as outras, largura usa HUD_BAR_W)

    # ── Tooltip genérico (skill, item, mundo) ──────────────────────────────
    # Arquivo: client/tooltip_handlers.py — função _draw_tooltip(), usada por
    # TODOS os tipos de tooltip do jogo (não tem uma constante por tipo).
    TOOLTIP_PAD        = 8     # respiro entre a borda da caixinha do tooltip e o texto
    TOOLTIP_LINE_EXTRA = 3     # espaço extra somado à altura da fonte entre cada linha do tooltip
    TOOLTIP_COL_GAP    = 20    # espaço mínimo entre a coluna esquerda e direita em linhas "label : valor"
    TOOLTIP_MAX_W      = 420   # largura MÁXIMA da caixa — texto de uma linha que passar disso quebra automaticamente em mais linhas

    # ── Reservas de "área segura" (painéis não podem aparecer em cima) ─────
    # Ver GameEngine._safe_panel_origin / UIScaleMixin._safe_panel_origin —
    # usado pelos painéis modais centralizados (não pelo HUD/hotbar/minimapa
    # em si, que são os elementos sendo protegidos).
    HUD_SAFE_W     = 360   # quanto espaço reservar na ESQUERDA da tela pro HUD não ficar embaixo de um painel
    MINIMAP_SAFE_W = 240   # quanto espaço reservar na DIREITA da tela pro minimapa não ficar embaixo de um painel

    # ── Offsets de posição — desloca o painel a partir do centro ───────────
    # Por padrão todo painel é CENTRALIZADO (na tela inteira, ou na área
    # livre que exclui HUD/minimapa — ver acima). Esses offsets somam um
    # deslocamento em PIXELS DE TELA (não passam por self._u() — não escalam
    # com a "Escala da UI", é deslocamento fixo igual em qualquer escala) a
    # partir desse centro: X positivo move pra DIREITA, X negativo pra
    # ESQUERDA; Y positivo move pra BAIXO, Y negativo pra CIMA. (0, 0) =
    # comportamento de sempre (centralizado, sem desvio). Mude um valor aqui
    # e o painel correspondente já nasce deslocado — não precisa editar
    # nenhum outro arquivo.
    INVENTORY_OFFSET_X    = -60
    INVENTORY_OFFSET_Y    = -30
    DEBUG_OFFSET_X        = 0
    DEBUG_OFFSET_Y         = 0
    HABILIDADES_OFFSET_X  = 0
    HABILIDADES_OFFSET_Y  = 0
    HOTBAR_EDITOR_OFFSET_X = 0
    HOTBAR_EDITOR_OFFSET_Y = 0
    CRAFTING_OFFSET_X     = 0
    CRAFTING_OFFSET_Y     = 0
    TRAINER_OFFSET_X      = 0
    TRAINER_OFFSET_Y      = 0
    SHOP_OFFSET_X         = 0
    SHOP_OFFSET_Y         = 0
    SHOP_QTY_MODAL_OFFSET_X = 0
    SHOP_QTY_MODAL_OFFSET_Y = 0
    QUEST_DIALOG_OFFSET_X  = 0
    QUEST_DIALOG_OFFSET_Y  = 0
    QUEST_JOURNAL_OFFSET_X = 0
    QUEST_JOURNAL_OFFSET_Y = 0
    TALENTS_OFFSET_X      = 0
    TALENTS_OFFSET_Y      = 0
    SKILL_LEVELS_OFFSET_X = 0
    SKILL_LEVELS_OFFSET_Y = 0
    TRADE_OFFSET_X        = 0
    TRADE_OFFSET_Y        = 0
    CHAT_OFFSET_X         = 0
    CHAT_OFFSET_Y         = 0
    MENU_QUIT_CONFIRM_OFFSET_X = 0
    MENU_QUIT_CONFIRM_OFFSET_Y = 0
    MENU_MAIN_OFFSET_X    = 0
    MENU_MAIN_OFFSET_Y    = 0
    MENU_RESOLUTION_OFFSET_X = 0
    MENU_RESOLUTION_OFFSET_Y = 0
    MENU_INTERFACE_OFFSET_X = 0
    MENU_INTERFACE_OFFSET_Y = 0
    MENU_SOUND_OFFSET_X   = 0
    MENU_SOUND_OFFSET_Y   = 0

    # ── Tamanho base das fontes (escala 1.0) ────────────────────────────────
    # Arquivo: game.py (_reload_ui_fonts). self.font_xs/sm/md/lg em todo o
    # client vêm daqui — aumentar um valor aqui deixa TODO texto que usa
    # aquele tamanho de fonte maior, em todos os painéis ao mesmo tempo.
    FONT_XS = 18   # texto bem pequeno (dicas, rodapés)
    FONT_SM = 22   # texto padrão da maioria dos painéis (nomes de item, stats, linhas de lista)
    FONT_MD = 28   # títulos de painel, botões, texto de destaque
    FONT_LG = 36   # títulos grandes (tela de loading, "RPG Online")
