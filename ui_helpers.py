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
    "common":   (200, 200, 200),
    "uncommon": (80, 200, 80),
    "rare":     (80, 120, 255),
    "epic":     (180, 80, 220),
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
    """Desenha 'xN' no canto inferior direito do ícone para itens stackáveis.

    Chame após blit do ícone. Não faz nada se max_stack == 1.
    """
    import pygame
    max_stack = getattr(item, "max_stack", 1)
    if max_stack <= 1:
        return
    stack = getattr(item, "stack", 1)
    text  = f"x{stack}"
    # Sombra para legibilidade sobre qualquer cor de fundo
    shadow = font.render(text, True, (0, 0, 0))
    label  = font.render(text, True, (255, 255, 255))
    x = rect.right  - label.get_width()  - 2
    y = rect.bottom - label.get_height() - 1
    surf.blit(shadow, (x + 1, y + 1))
    surf.blit(label,  (x, y))


def item_tooltip_lines(item):
    """
    Build tooltip body lines for an item.
    Returns a list of either:
      (text, color)                              -- single-column line
      ((left_text, left_color), (right_text, right_color))  -- two-column line
    The title (item.name) is NOT included; pass it separately to _draw_tooltip.
    """
    lines = []

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
    else:
        lines.append((f"Slot: {item.slot}", (160, 130, 80)))

    rar_col = RARITY_COLORS.get(item.rarity, (200, 200, 200))
    lines.append((f"Raridade: {item.rarity.capitalize()}", rar_col))

    for m in item.modifiers:
        val = m.value
        if isinstance(val, float) and val == int(val):
            val_str = f"+{int(val)}"
        elif isinstance(val, float):
            val_str = f"+{val:.2f}"
        else:
            val_str = f"+{val}"
        lines.append((f"{val_str} {m.attribute}", (130, 200, 130)))

    if item.proc:
        chance_pct = int(item.proc.get("chance", 0) * 100)
        lines.append((f"Proc ({chance_pct}%): {item.proc['label']}", (180, 120, 220)))

    return lines
