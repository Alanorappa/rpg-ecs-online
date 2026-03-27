# ui_helpers.py
"""Shared UI helpers used by game.py and systems.py."""

RARITY_COLORS = {
    "common":   (200, 200, 200),
    "uncommon": (80, 200, 80),
    "rare":     (80, 120, 255),
    "epic":     (180, 80, 220),
}


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
        if consumable.get("heal_instant", 0):
            lines.append((f"Cura {consumable['heal_instant']} HP instantaneamente", (80, 220, 120)))
        if consumable.get("heal_per_tick", 0) and consumable.get("ticks", 0):
            total = consumable["heal_per_tick"] * consumable["ticks"]
            dur   = int(consumable["interval"] * consumable["ticks"])
            lines.append((f"Cura {consumable['heal_per_tick']} HP a cada {consumable['interval']:.0f}s", (80, 220, 120)))
            lines.append((f"Total: {total} HP em {dur}s", (60, 180, 100)))
        if consumable.get("ooc_only"):
            lines.append(("Apenas fora de combate", (220, 160, 60)))
    elif item.item_type == "weapon":
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
