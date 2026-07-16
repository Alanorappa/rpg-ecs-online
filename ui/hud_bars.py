"""
hud_bars.py — HUD de nível+recursos desenhada acima do sprite do
player/mob, a partir dos assets desenhados pelo usuário
(assets/hud/player_hud_bar.png, mob_hud_bar.png).

Causa raiz de um bug real (11/07/2026, corrigido aqui): a primeira versão
desenhava o FUNDO+BARRAS em espaço de mundo (escala com o zoom da câmera)
e o NÚMERO DO NÍVEL em espaço de tela via WORLD_LABELS (tamanho fixo,
nunca escala) — dois sistemas de escala DIFERENTES pro mesmo elemento
visual. Number "99" (2 dígitos) não cabia no quadrado nativo de 12px do
mob, e em zoom baixo a arte ficava pequena mas o texto continuava do
mesmo tamanho — o número "vazava" pra fora da caixinha, parecendo
flutuar desconectado da HUD.

Fix: fundo+barras+número do nível viram UMA ÚNICA Surface composta aqui
(`build_player_hud`/`build_mob_hud`), sempre em ESCALA DE TELA FIXA
(`SCALE`, nearest-neighbor — não é "distorção", é upscale de pixel art
igual qualquer sprite ampliado, só nunca fica pequeno demais pro número
caber). Essa Surface vai inteira pro WORLD_LABELS (screen-space, pós-zoom
— mesmo sistema do nome, ver ARQUITETURA_ONLINE.md 23.5) como um ícone
só: nunca mais dá pra desalinhar arte vs. número, porque nascem juntos na
mesma Surface, no mesmo pixel.

Coordenadas dos assets mapeadas pixel a pixel (RLE por linha de cada PNG)
e validadas com uma imagem de debug (contorno colorido sobre o asset
ampliado 8x, nearest-neighbor) — nunca escolhidas de olho.
"""
from __future__ import annotations
import pygame
from paths import resource_path

# Fator de upscale (nearest-neighbor) aplicado à Surface composta inteira
# (fundo+barras+número) — só existe pra não ficar pequeno demais pro
# número de 2 dígitos caber dentro do quadrado do nível (bug relatado
# 11/07/2026). Não é escala de câmera, não muda com zoom.
# Valor testado visualmente lado a lado (1/1.5/2/3, ver conversa
# 11/07/2026): SCALE=3 deixava a HUD grande demais (usuário pediu pra
# reverter o tamanho); SCALE=1 (nativo) não cabe nem fonte pequena sem
# vazar. 2 é o menor valor que ainda cabe "99" sem estourar a caixa.
SCALE = 2

# ── Cores ────────────────────────────────────────────────────────────────
HP_COLOR = (0, 200, 60)      # mesma cor já usada na barra de HP antiga
XP_COLOR = (190, 140, 230)   # roxo claro (pedido do usuário 11/07/2026)
# Cor da barra de HP do mob por DISPOSIÇÃO — mesmos 3 tiers de
# content/faction_data.py (hostil/neutro/amigavel), resolvidos contra
# PLAYER_FACTION do ponto de vista de quem está olhando. Pedido do
# usuário 15/07/2026, depois de testar hostil/neutro pela primeira vez
# (Lobo neutro em map_1): "hostis a barra de HP é vermelha, neutros é
# amarela clara, e NPCs amigáveis terão a barra verde" — "amigavel" reusa
# o HP_COLOR verde de sempre (não é exclusivo de NPC: qualquer mob cuja
# facção resolva amigavel usa a mesma cor, ver DISPOSITION_HP_COLORS).
DISPOSITION_HP_COLORS = {
    "hostil":   (200, 40, 40),
    "neutro":   (235, 220, 110),
    "amigavel": HP_COLOR,
}
# Mesma cor de cada recurso já usada no HUD lateral (client/hud_handlers.py)
# — não inventa cor nova, só reaproveita (C_RED/C_ORANGE de client/colors.py).
# Pedido do usuário 15/07/2026: raiva vira vermelha (era laranja, confundia
# com a própria concentração) e concentração vira laranja forte (era azul,
# confundia com mana).
RESOURCE_COLORS = {
    "mago":      (50, 100, 255),   # barra de mana (inalterada)
    "arqueiro":  (255, 160, 0),    # barra de concentração — C_ORANGE
    "guerreiro": (220, 50, 50),    # barra de raiva — C_RED
}
LEVEL_TEXT_COLOR = (255, 240, 200)
# Tamanho da fonte pixel (ui/fonts.py::make_pixel) pro número do nível —
# escolhido testando visualmente ao lado de SCALE (não por cálculo). 16
# ainda cabe sem vazar com SCALE=2 e usa melhor o espaço que sobrava com
# 14 (pedido do usuário 11/07/2026). 18 já começa a tocar a borda do
# círculo do mob (a restrição mais apertada) — não usar sem revisar
# M_LEVEL_BOX/SCALE juntos.
LEVEL_FONT_SIZE = 16

# ── Coordenadas nativas (ver validação na conversa 11/07/2026) ───────────
# player_hud_bar.png — 64x16
P_SIZE      = (64, 16)
P_LEVEL_BOX = (1, 1, 14, 14)     # x0, y0, x1, y1 (inclusive)
# A borda direita do asset NÃO é reta — mapeada pixel a pixel (15/07/2026,
# scan automatizado por linha do último pixel de interior não-preto):
# linha da XP (y=3) termina em x=61; linhas de HP (y=5..8) e recurso
# (y=10..11) terminam em x=62. Um X1 único (62) pra tudo — ajuste anterior
# pro relato "barra cheia terminava 1px antes da borda" — corrigia HP/
# recurso mas fazia a barra de XP pintar por cima do próprio contorno
# (mesma classe do bug do mob abaixo, achado ao investigar o relato do
# usuário 15/07/2026 sobre a barra do MOB). P_BAR_X0/X1 seguem servindo
# HP e recurso; XP usa o par dedicado P_XP_X0/X1.
P_BAR_X0, P_BAR_X1 = 16, 62
P_XP_X0,  P_XP_X1  = 16, 61
P_XP_Y  = (3, 3)
P_HP_Y  = (5, 8)
# (10, 11): 2 linhas de preenchimento — linha 12 é a borda preta INFERIOR
# do asset (confirmado no mapeamento pixel a pixel), não faz parte da
# barra. Bug real (11/07/2026): incluir a linha 12 no fill pintava por
# cima do contorno preto de baixo, "tampando" a borda da HUD.
P_RES_Y = (10, 11)

# mob_hud_bar.png — 48x12
M_SIZE      = (48, 12)
M_LEVEL_BOX = (0, 1, 11, 10)
# M_HP_X1 = 46: revertido de 47 (15/07/2026). O scan pixel a pixel (ver
# comentário de P_BAR_X1 acima) mostra que o último pixel de INTERIOR da
# barra de HP do mob (linhas y=5,6) é x=46 — x=47 já É o pixel de borda
# preta. O ajuste anterior (46→47, mesma rodada do fix da barra do
# player) usou "último índice válido da arte" em vez do último índice de
# INTERIOR — estavam desalinhados pra este asset especificamente (o do
# player por coincidência tem HP/recurso terminando exatamente no último
# índice da arte, mas o do mob não). Causa raiz do bug relatado pelo
# usuário: a barra a 100% pintava por cima do próprio contorno direito,
# "sumindo" a borda.
M_HP_X0, M_HP_X1 = 12, 46
M_HP_Y = (5, 6)

# Respiro fixo (px de tela) entre sprite→HUD e HUD→fila de efeitos — usado
# tanto no gap_before do WORLD_LABELS.add_icon() da HUD quanto no cálculo
# de effects_row_offset() abaixo, pra manter os dois consistentes sem
# duplicar o número em cada call site.
HUD_GAP_PX     = 4
EFFECTS_GAP_PX = 4

_player_art: "pygame.Surface | None" = None
_mob_art:    "pygame.Surface | None" = None


def effects_row_offset(hud_surf: "pygame.Surface") -> tuple:
    """(x_offset, y_offset) em PX DE TELA pra
    WORLD_LABELS.add_icon_offset() encostar a fila de ícones de efeito na
    borda direita da HUD, centralizada verticalmente — a partir do MESMO
    (world_x, world_y) já usado no add_icon() da própria HUD (mesmo
    stack_key). Só funciona se a HUD foi enfileirada com
    `gap_before=HUD_GAP_PX` (senão os dois desalinham).

    world_x é o CENTRO da HUD (mesma âncora usada em add_icon() pra
    desenhar a própria HUD centralizada) — bug real (11/07/2026): usava
    `hud_surf.get_width()` (largura INTEIRA) pra ir do centro até a borda
    direita, quando precisa de só METADE da largura; sobrava um vão do
    tamanho da HUD inteira entre ela e a fila de efeitos."""
    xo = hud_surf.get_width() / 2 + EFFECTS_GAP_PX
    yo = -HUD_GAP_PX - hud_surf.get_height() / 2
    return xo, yo


def _load() -> None:
    global _player_art, _mob_art
    if _player_art is None:
        _player_art = pygame.image.load(
            resource_path("assets/hud/player_hud_bar.png")).convert_alpha()
    if _mob_art is None:
        _mob_art = pygame.image.load(
            resource_path("assets/hud/mob_hud_bar.png")).convert_alpha()


def _fill_row(surf, x_range: tuple, y_range: tuple, ratio: float, color: tuple) -> None:
    bar_w = x_range[1] - x_range[0] + 1
    fw = max(0, min(bar_w, int(bar_w * max(0.0, min(1.0, ratio)))))
    if fw > 0:
        pygame.draw.rect(surf, color,
                         (x_range[0], y_range[0], fw, y_range[1] - y_range[0] + 1))


def _level_center_px(box: tuple) -> tuple:
    x0, y0, x1, y1 = box
    return (x0 + x1) / 2 * SCALE, (y0 + y1) / 2 * SCALE


# Cache do número de nível já renderizado + centro de tinta (via
# pygame.mask) por (level, id(font)) — build_player_hud/build_mob_hud
# rodam TODO FRAME pra cada entidade visível com barra de HP (player +
# todo mob no campo de visão), e o par font.render()+pygame.mask.from_
# surface()+get_bounding_rects() é o custo dominante da função (scan de
# pixel a pixel pra achar a tinta real). Level muda raríssimo (level up),
# então cachear por level elimina esse custo em todo frame onde nada
# mudou — bug de performance real reportado pelo usuário (13/07/2026,
# queda de FPS ao andar com o arqueiro: mais mobs em campo de visão =
# mais chamadas repetindo o mesmo mask scan sem necessidade).
_level_surf_cache: dict = {}
_LEVEL_CACHE_MAX = 256


def _level_surf(level: int, font) -> tuple:
    """(surf, ink_cx, ink_cy) — ink_cx/cy = centro da tinta REAL (não do
    tamanho nominal da Surface, que inclui espaço de acento/descendente
    que dígitos não usam) relativo ao canto (0,0) de `surf`. Cacheado."""
    key = (level, id(font))
    cached = _level_surf_cache.get(key)
    if cached is not None:
        return cached
    surf = font.render(str(level), False, LEVEL_TEXT_COLOR)
    mask = pygame.mask.from_surface(surf)
    rects = mask.get_bounding_rects()
    if not rects:
        ink = surf.get_rect()
    else:
        ink = rects[0]
        for r in rects[1:]:
            ink = ink.union(r)
    if len(_level_surf_cache) >= _LEVEL_CACHE_MAX:
        _level_surf_cache.clear()
    result = (surf, ink.centerx, ink.centery)
    _level_surf_cache[key] = result
    return result


def _blit_level_number(dest_surf, level: int, font, cx: float, cy: float) -> None:
    """Centraliza o número do nível em (cx, cy) usando o bounding box REAL
    da tinta — bug relatado pelo usuário 11/07/2026 ("não parece
    centralizado") — via _level_surf(), cacheado (ver docstring lá)."""
    surf, ink_cx, ink_cy = _level_surf(level, font)
    dest_surf.blit(surf, (cx - ink_cx, cy - ink_cy))


def build_player_hud(hp_ratio: float, xp_ratio: float, resource_ratio: float,
                     resource_color: tuple, level: int, level_font,
                     hp_color: tuple = HP_COLOR) -> "pygame.Surface":
    """Monta fundo+barras+número do nível numa Surface só, já no tamanho
    de tela final (SCALE fixo, nearest-neighbor). Caller usa
    surf.get_width()/get_height() pra saber onde encostar outros
    elementos (ex: fila de efeitos na borda direita, via
    ui/world_labels.py::add_icon_offset) — não precisa devolver isso à
    parte, é só ler da Surface. `hp_color` (default verde de sempre):
    disposição de player remoto — oponente de duelo fica vermelho
    (DISPOSITION_HP_COLORS["hostil"]), mesmo esquema do mob."""
    _load()
    w, h = P_SIZE
    base = pygame.Surface((w, h), pygame.SRCALPHA)
    base.blit(_player_art, (0, 0))
    _fill_row(base, (P_XP_X0, P_XP_X1),   P_XP_Y,  xp_ratio,       XP_COLOR)
    _fill_row(base, (P_BAR_X0, P_BAR_X1), P_HP_Y,  hp_ratio,       hp_color)
    _fill_row(base, (P_BAR_X0, P_BAR_X1), P_RES_Y, resource_ratio, resource_color)

    big = pygame.transform.scale(base, (w * SCALE, h * SCALE))
    lcx, lcy = _level_center_px(P_LEVEL_BOX)
    _blit_level_number(big, level, level_font, lcx, lcy)
    return big


def build_mob_hud(hp_ratio: float, level: int, level_font,
                  hp_color: tuple = HP_COLOR) -> "pygame.Surface":
    """Mesma ideia de build_player_hud, só com barra de HP (mobs não têm
    XP/recurso — pedido do usuário: "no caso dos mobs é a barra de hp e o
    level somente"). `hp_color` default mantém compatibilidade com quem
    não resolve disposição (ex: boneco de treino) — caller com facção
    disponível deve passar `DISPOSITION_HP_COLORS[tier]`."""
    _load()
    w, h = M_SIZE
    base = pygame.Surface((w, h), pygame.SRCALPHA)
    base.blit(_mob_art, (0, 0))
    _fill_row(base, (M_HP_X0, M_HP_X1), M_HP_Y, hp_ratio, hp_color)

    big = pygame.transform.scale(base, (w * SCALE, h * SCALE))
    lcx, lcy = _level_center_px(M_LEVEL_BOX)
    _blit_level_number(big, level, level_font, lcx, lcy)
    return big


# Largura nativa do círculo de nível dentro de mob_hud_bar.png — recorte
# ANTES da coluna divisória sólida que separa o badge da barra de HP
# (linha 4 do asset é preta em toda a largura, confirmado no mapeamento
# pixel a pixel; o círculo em si nunca ultrapassa essa coluna). Reaproveita
# o MESMO asset do mob — não é um PNG novo.
_NPC_BADGE_W = 12


def build_npc_badge(level: int, level_font) -> "pygame.Surface":
    """Nameplate de NPC não-combatente (vendedor/quest-giver/ferreiro/
    treinador): só o badge de nível (círculo + número), SEM barra de HP —
    não faz sentido pra quem não tem CombatStats. Recortado do mesmo
    asset `mob_hud_bar.png` que os mobs usam (build_mob_hud), pra ficar
    visualmente idêntico ao badge deles — pedido do usuário 15/07/2026:
    "NPCs também quero que tenham nameplates igual aos mobs"."""
    _load()
    h = M_SIZE[1]
    badge_native = _mob_art.subsurface(pygame.Rect(0, 0, _NPC_BADGE_W, h)).copy()
    big = pygame.transform.scale(badge_native, (_NPC_BADGE_W * SCALE, h * SCALE))
    lcx, lcy = _level_center_px(M_LEVEL_BOX)
    _blit_level_number(big, level, level_font, lcx, lcy)
    return big
