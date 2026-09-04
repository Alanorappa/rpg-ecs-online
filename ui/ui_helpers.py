# ui_helpers.py
"""Shared UI helpers used by game.py and systems.py."""

# ---------------------------------------------------------------------------
# fill_surf — cache de Surfaces SRCALPHA pré-preenchidas
# ---------------------------------------------------------------------------
# Painéis de UI alocavam pygame.Surface(size, SRCALPHA) + fill() TODO frame
# (overlay full-screen, fundo de painel, fundo de tooltip) — alocação de
# MB/frame e spikes medidos (hud:talents 13ms). Cache por (size, rgba):
# o conteúdo é imutável por construção, então compartilhar é seguro DESDE
# QUE o consumidor não mute (não fazer set_alpha/fill/blit NELA — só blitá-la).
_FILL_CACHE: dict = {}
_FILL_CACHE_MAX = 256


def fill_surf(size: tuple, rgba: tuple):
    """Surface SRCALPHA de `size` preenchida com `rgba`, cacheada.

    COMPARTILHADA — não mutar; use .copy() se precisar desenhar em cima.
    """
    import pygame
    key = (size, rgba)
    s = _FILL_CACHE.get(key)
    if s is None:
        if len(_FILL_CACHE) >= _FILL_CACHE_MAX:
            _FILL_CACHE.clear()
        s = pygame.Surface(size, pygame.SRCALPHA)
        s.fill(rgba)
        _FILL_CACHE[key] = s
    return s


RARITY_COLORS = {
    "common":    (200, 200, 200),
    "uncommon":  (80, 200, 80),
    "rare":      (80, 120, 255),
    "epic":      (180, 80, 220),
    "legendary": (224, 135, 47),
    "mythic":    (221, 68, 68),
}


def wrap_text(text: str, font, max_px: int) -> list:
    """Quebra `text` em linhas que cabem em `max_px` pixels com `font`,
    quebrando só em espaços (word-wrap). Usado por qualquer painel/tooltip
    que precise impor uma largura máxima a texto livre — ver
    arquitetura/PROBLEMAS_ARQUITETURA.md item IU4 (tooltip sem limite de
    largura, virava uma linha só gigante)."""
    words = text.split()
    lines, cur = [], ""
    for word in words:
        test = cur + (" " if cur else "") + word
        if font.size(test)[0] <= max_px:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_stack_count(surf, item, rect, font) -> None:
    """Desenha o número da stack (sem prefixo 'x') no canto inferior
    direito do ícone para itens stackáveis com 2+ unidades atuais.

    Chame após blit do ícone. Não desenha nada se max_stack == 1 (item
    não-stackável) nem se stack <= 1 (28/07/2026, pedido do usuário —
    "1" é redundante, todo item já tem pelo menos 1; só mostra a partir
    de 2 de verdade empilhadas).
    """
    import pygame
    max_stack = getattr(item, "max_stack", 1)
    if max_stack <= 1:
        return
    stack = getattr(item, "stack", 1)
    if stack <= 1:
        return
    text  = str(stack)
    label   = font.render(text, False, (255, 255, 255))
    outline = font.render(text, False, (0, 0, 0))
    x = rect.right  - label.get_width()  - 2
    y = rect.bottom - label.get_height() - 1
    # Outline preto nas 8 direções — legível sobre qualquer cor de fundo
    # (antes era só 1 sombra deslocada num canto, cobria só 1 lado).
    for dx, dy in ((-1, -1), (0, -1), (1, -1),
                   (-1,  0),          (1,  0),
                   (-1,  1), (0,  1), (1,  1)):
        surf.blit(outline, (x + dx, y + dy))
    surf.blit(label, (x, y))


# Atributos guardados como fração 0..1 no Modifier (ex: crit_rating=0.06) —
# mostrar como inteiro percentual (+6%), nunca a fração crua (+0.06). Os
# demais ratings (dodge/parry/block/haste) já são pontos inteiros na fonte
# dos dados (ver stat_fns._STAT_CLAMPS) — não precisam dessa conversão.
_PERCENT_FRACTION_ATTRS = frozenset({"crit_rating"})

_MATERIAL_LABELS = {"placa": "Placa", "couro": "Couro", "tecido": "Tecido"}


def item_tooltip_lines(item, viewer_class_id: str | None = None):
    """
    Build tooltip body lines for an item.
    Returns a list of either:
      (text, color)                              -- single-column line
      ((left_text, left_color), (right_text, right_color))  -- two-column line
    The title (item.name) is NOT included; pass it separately to _draw_tooltip.

    viewer_class_id: classe do personagem que está vendo o tooltip (dono do
    inventário sendo olhado, sempre o player local) — usada só pra colorir a
    linha de material de armadura (verde/vermelho conforme
    stats_system.CLASS_ARMOR_ALLOWED). None = sem checagem (mostra neutro).
    """
    lines = []

    rar_col = RARITY_COLORS.get(item.rarity, (200, 200, 200))
    lines.append((f"Raridade: {item.rarity.capitalize()}", rar_col))

    # Item que concede quest ao ser saqueado (Fase M4, 25/07/2026, pedido do
    # usuário) — tag automática, detectada via ITEM_GRANTS_QUEST (fonte
    # única, mesma usada pelo gancho real em server/loot_processor.py::
    # request_loot) em vez de exigir escrever isso à mão na description de
    # cada item — evita o item dizer "inicia quest" e o mapa esquecer de
    # registrar (ou vice-versa).
    from content.quests_data import ITEM_GRANTS_QUEST as _IGQ_tt
    if item.name in _IGQ_tt:
        lines.append(("Este item inicia uma quest", (255, 215, 100)))

    consumable = getattr(item, "consumable", None)

    if consumable:
        lines.append(("Consumível", (180, 220, 180)))
        _desc = consumable.get("desc", "")
        if _desc:
            # Descrição personalizada — não exibe detalhes automáticos (redundantes)
            lines.append((_desc, (200, 200, 200)))
        else:
            # Sem desc: gera linhas automáticas com os dados do consumível
            if consumable.get("heal_instant", 0):
                lines.append((f"Cura {consumable['heal_instant']} HP instantaneamente", (80, 220, 120)))
            if consumable.get("heal_per_tick", 0) and consumable.get("ticks", 0):
                total = consumable["heal_per_tick"] * consumable["ticks"]
                dur   = int(consumable["interval"] * consumable["ticks"])
                lines.append((f"Cura {consumable['heal_per_tick']} HP a cada {consumable['interval']:.0f}s", (80, 220, 120)))
                lines.append((f"Total: {total} HP em {dur}s", (60, 180, 100)))
            if consumable.get("mana_restore", 0):
                lines.append((f"Restaura {consumable['mana_restore']} de mana instantaneamente", (100, 180, 255)))
            if consumable.get("mana_per_tick", 0) and consumable.get("ticks", 0):
                total_m = consumable["mana_per_tick"] * consumable["ticks"]
                dur_m   = int(consumable["interval"] * consumable["ticks"])
                lines.append((f"Restaura {consumable['mana_per_tick']} de mana a cada {consumable['interval']:.0f}s", (100, 180, 255)))
                lines.append((f"Total: {total_m} de mana em {dur_m}s", (80, 150, 220)))
            if consumable.get("ooc_only"):
                lines.append(("Apenas fora de combate", (220, 160, 60)))
    elif item.item_type == "ammo":
        lines.append(("Munição · Bag", (160, 130, 80)))
        lines.append((f"Quantidade: {item.stack}/{item.max_stack}", (200, 160, 80)))
        if item.damage_max > 0:
            lines.append((f"Bônus de dano: +{item.damage_min}–{item.damage_max}", (220, 160, 80)))
    elif item.item_type == "quiver":
        lines.append(("Mão Secundária · Aljava", (160, 130, 80)))
        _max_a = getattr(item, "max_arrows", 0) or 100
        _cur_a = getattr(item, "arrow_count", 0)
        _pct   = int(_cur_a / _max_a * 100)
        _col   = (80, 200, 80) if _pct >= 50 else (220, 160, 60) if _pct >= 20 else (220, 80, 80)
        lines.append((f"Flechas: {_cur_a}/{_max_a}  ({_pct}%)", _col))
        if item.damage_max > 0:
            lines.append((f"Flecha carregada: +{item.damage_min}–{item.damage_max} dano", (220, 160, 80)))
    elif item.item_type == "weapon":
        if getattr(item, "subtype", "") == "Bow":
            lines.append((("Ranged · Two Hand", (160, 130, 80)), ("Arco", (200, 200, 200))))
            if item.damage_min > 0:
                dmg_str = f"{item.damage_min} - {item.damage_max} Dano"
                spd_str = f"Vel. {item.attack_speed:.1f}s"
                lines.append(((dmg_str, (200, 180, 100)), (spd_str, (200, 180, 100))))
            _range = getattr(item, "cast_range", 0)
            if _range:
                lines.append((f"Alcance: {_range} tiles", (160, 200, 220)))
        else:
            hand = "Two Hand" if item.two_handed else "One Hand"
            sub  = item.subtype if item.subtype else "Weapon"
            lines.append(((hand, (160, 130, 80)), (sub, (200, 200, 200))))
            if item.damage_min > 0:
                dmg_str = f"{item.damage_min} - {item.damage_max} Damage"
                spd_str = f"Speed {item.attack_speed:.1f}"
                lines.append(((dmg_str, (200, 180, 100)), (spd_str, (200, 180, 100))))
    elif item.item_type == "shield":
        hand = "Two Hand" if item.two_handed else "Off Hand"
        if viewer_class_id is not None:
            from engine.stats_system import is_weapon_allowed_for_class
            shield_col = (80, 220, 80) if is_weapon_allowed_for_class(item, viewer_class_id) else (220, 80, 80)
        else:
            shield_col = (200, 200, 200)
        lines.append(((hand, (160, 130, 80)), ("Escudo", shield_col)))
    elif item.item_type == "armor":
        mat = getattr(item, "armor_class", "")
        mat_label = _MATERIAL_LABELS.get(mat, mat or "—")
        if mat and viewer_class_id is not None:
            from engine.stats_system import CLASS_ARMOR_ALLOWED
            allowed = mat in CLASS_ARMOR_ALLOWED.get(viewer_class_id, frozenset())
            mat_col = (80, 220, 80) if allowed else (220, 80, 80)
        else:
            mat_col = (200, 200, 200)
        slot_label = item.slot.capitalize() if item.slot else "—"
        lines.append(((slot_label, (160, 130, 80)), (mat_label, mat_col)))
    else:
        lines.append((f"Slot: {item.slot}", (160, 130, 80)))

    item_level = getattr(item, "item_level", 1)
    lines.append((f"Nível do Item {item_level}", (217, 172, 66)))

    for m in item.modifiers:
        val = m.value
        if m.attribute in _PERCENT_FRACTION_ATTRS:
            val_str = f"+{round(val * 100)}%"
        elif isinstance(val, float) and val == int(val):
            val_str = f"+{int(val)}"
        elif isinstance(val, float):
            val_str = f"+{val:.2f}"
        else:
            val_str = f"+{val}"
        lines.append((f"{val_str} {m.attribute}", (130, 200, 130)))

    level_req = getattr(item, "level_requirement", 1)
    lines.append((f"Requer Nível {level_req}", (170, 160, 145)))

    if item.proc:
        chance_pct = int(item.proc.get("chance", 0) * 100)
        lines.append((f"Proc ({chance_pct}%): {item.proc['label']}", (180, 120, 220)))

    lines.append((f"Valor de venda: {item.value}g", (150, 130, 90)))

    description = getattr(item, "description", "")
    if description:
        lines.append((description, (150, 140, 160)))

    return lines


# ---------------------------------------------------------------------------
# Primitivas de desenho pro rastreio de quest no mapa/minimapa (03/09/2026)
# ---------------------------------------------------------------------------
# pygame não tem círculo/retângulo tracejado nativo, nem fill translúcido
# numa Surface sem SRCALPHA — únicas 3 funções que sabem desenhar isso,
# usadas por ui/minimap.py e ui/map_overlay.py (nunca duplicar o cálculo
# de tracejado/alpha em cada um dos dois).

def draw_dashed_circle(surface, color, center, radius, dash_deg=18, gap_deg=10, width=2) -> None:
    """Contorno tracejado — arcos curtos ao redor da circunferência.
    `radius` já em px de tela (quem chama já converteu tile→tela)."""
    import math
    import pygame
    r = int(radius)
    if r <= 0:
        return
    rect = pygame.Rect(center[0] - r, center[1] - r, r * 2, r * 2)
    step = dash_deg + gap_deg
    angle = 0.0
    while angle < 360.0:
        start = math.radians(angle)
        end = math.radians(min(angle + dash_deg, 360.0))
        pygame.draw.arc(surface, color, rect, start, end, width)
        angle += step


def draw_translucent_filled_circle(surface, color, center, radius, alpha=128) -> None:
    """Preenchimento translúcido — desenha num Surface temporário SRCALPHA
    (a Surface alvo pode não ter canal alpha) e blita por cima."""
    import pygame
    r = int(radius)
    if r <= 0:
        return
    tmp = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
    pygame.draw.circle(tmp, (*color[:3], alpha), (r, r), r)
    surface.blit(tmp, (center[0] - r, center[1] - r))


def draw_dashed_rect(surface, color, rect, dash_len=6, gap_len=4, width=2) -> None:
    """Contorno tracejado de um pygame.Rect — pygame.draw.rect não tem
    estilo tracejado."""
    import math
    import pygame

    def _dashed_line(p0, p1):
        x0, y0 = p0
        x1, y1 = p1
        length = math.hypot(x1 - x0, y1 - y0)
        if length == 0:
            return
        dx, dy = (x1 - x0) / length, (y1 - y0) / length
        dist = 0.0
        while dist < length:
            seg_end = min(dist + dash_len, length)
            pygame.draw.line(surface, color,
                             (x0 + dx * dist, y0 + dy * dist),
                             (x0 + dx * seg_end, y0 + dy * seg_end), width)
            dist += dash_len + gap_len

    _dashed_line(rect.topleft, rect.topright)
    _dashed_line(rect.topright, rect.bottomright)
    _dashed_line(rect.bottomright, rect.bottomleft)
    _dashed_line(rect.bottomleft, rect.topleft)


def draw_box_tooltip(screen, font_title, font_body, x: int, y: int,
                     title: str, lines: list) -> None:
    """Caixa de tooltip genérica — MESMO estilo visual do tooltip de skill/
    efeito (client/tooltip_handlers.py: fundo translúcido (10,8,5,220) +
    borda 1px (120,90,50) border_radius=3). Usada pro hover em cima de um
    círculo de rastreio de quest no mapa/minimapa; título em destaque,
    `lines` (list[str]) no corpo. Não deixa a caixa sair da tela."""
    import pygame
    PAD = 6
    title_surf = font_title.render(title, False, (230, 210, 150))
    line_surfs = [font_body.render(t, False, (210, 210, 210)) for t in lines]
    w = max([title_surf.get_width()] + [s.get_width() for s in line_surfs]) + PAD * 2
    h = title_surf.get_height() + PAD * 2 + sum(s.get_height() + PAD for s in line_surfs)
    sw, sh = screen.get_size()
    tx = max(0, min(x, sw - w))
    ty = max(0, min(y, sh - h))
    screen.blit(fill_surf((w, h), (10, 8, 5, 220)), (tx, ty))
    pygame.draw.rect(screen, (120, 90, 50), (tx, ty, w, h), 1, border_radius=3)
    screen.blit(title_surf, (tx + PAD, ty + PAD))
    cy = ty + PAD + title_surf.get_height() + PAD
    for s in line_surfs:
        screen.blit(s, (tx + PAD, cy))
        cy += s.get_height() + PAD
