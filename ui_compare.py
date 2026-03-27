# ui_compare.py
"""Painel de comparação de item equipado vs item em hover.

Um único painel aparece ao lado do tooltip existente mostrando:
  - Ícone + nome do item equipado no mesmo slot (ou "Slot vazio")
  - Tipo de mão + subtipo (se arma)
  - Dano e velocidade de ataque (se arma física/mágica)
  - Cada atributo do item equipado com o Δ em relação ao item novo (em hover)
    Verde (+N ▲) = novo item é melhor
    Vermelho (-N ▼) = novo item é pior
    Cinza (=) = mesmo valor

Uso:
    draw_compare_panel(screen, font_sm, font_md, new_item, equipped_item, anchor_rect)
    anchor_rect = rect retornado por _draw_tooltip()
"""
from __future__ import annotations
import pygame

RARITY_COLORS = {
    "common":   (200, 200, 200),
    "uncommon": ( 30, 200,  30),
    "rare":     ( 80, 140, 255),
    "epic":     (180,  50, 255),
}

_ATTR_LABELS = {
    "attack_power": "Atq Físico",
    "spell_power":  "Atq Mágico",
    "armor":        "Armadura",
    "stamina":      "Estamina",
    "crit_rating":  "Crítico",
    "haste_rating": "Aceleração",
}

_PAD    = 8
_ROW_H  = 18
_ICON_S = 36
_HDR_H  = _ICON_S + _PAD
_W      = 260


def _weapon_virtuals(item, d: dict) -> None:
    """Adds __dano and __speed virtual keys for weapon damage comparison."""
    if item and item.item_type == "weapon" and item.damage_min > 0:
        d["__dano"]  = (item.damage_min + item.damage_max) / 2
        d["__speed"] = item.attack_speed


def draw_compare_panel(
    screen: pygame.Surface,
    font_sm: pygame.font.Font,
    font_md: pygame.font.Font,
    new_item,
    equipped_item,
    anchor_rect: pygame.Rect,
) -> None:
    """Renderiza um painel único ao lado do tooltip mostrando o item equipado com Δ."""
    from icon_manager import ICONS

    new_mods = {m.attribute: m.value for m in new_item.modifiers}
    eq_mods  = {m.attribute: m.value for m in equipped_item.modifiers} if equipped_item else {}

    _weapon_virtuals(new_item, new_mods)
    _weapon_virtuals(equipped_item, eq_mods)

    # Damage/speed rows first, then remaining attrs sorted
    prio      = [a for a in ("__dano", "__speed") if a in new_mods or a in eq_mods]
    rest      = sorted(a for a in set(list(new_mods) + list(eq_mods)) if a not in ("__dano", "__speed"))
    all_attrs = prio + rest

    # Extra header row when either item is a weapon
    show_weapon_hdr = (new_item.item_type == "weapon" or
                       (equipped_item and equipped_item.item_type == "weapon"))

    rows    = max(1, len(all_attrs)) + (1 if show_weapon_hdr else 0)
    lbl_h   = font_sm.get_height() + 2
    panel_h = _PAD + lbl_h + _ICON_S + _PAD + 1 + _PAD + rows * _ROW_H + _PAD

    sw, sh = screen.get_size()
    x = anchor_rect.right + 6
    if x + _W > sw:
        x = anchor_rect.left - _W - 6
    x = max(4, x)
    y = anchor_rect.top
    y = max(4, min(y, sh - panel_h - 4))

    bg = pygame.Surface((_W, panel_h), pygame.SRCALPHA)
    bg.fill((12, 8, 4, 235))
    screen.blit(bg, (x, y))
    border_col = (140, 100, 60) if equipped_item else (65, 48, 35)
    pygame.draw.rect(screen, border_col, (x, y, _W, panel_h), 1, border_radius=3)

    cy = y + _PAD

    # Label
    lbl = font_sm.render("Equipado atualmente", True, (130, 110, 80))
    screen.blit(lbl, (x + _PAD, cy))
    cy += lbl_h

    # Icon + name + rarity
    icon_r = pygame.Rect(x + _PAD, cy, _ICON_S, _ICON_S)
    if equipped_item:
        rc   = RARITY_COLORS.get(equipped_item.rarity, (100, 100, 100))
        surf = ICONS.get(ICONS.item_key(equipped_item), _ICON_S)
        if surf:
            screen.blit(surf, icon_r)
        else:
            pygame.draw.rect(screen, rc, icon_r, border_radius=2)
        name_col  = RARITY_COLORS.get(equipped_item.rarity, (200, 200, 200))
        name_surf = font_md.render(equipped_item.name[:16], True, name_col)
        rar_surf  = font_sm.render(equipped_item.rarity.capitalize(), True, name_col)
        screen.blit(name_surf, (icon_r.right + _PAD, cy))
        screen.blit(rar_surf,  (icon_r.right + _PAD, cy + name_surf.get_height() + 2))
    else:
        pygame.draw.rect(screen, (40, 30, 20), icon_r, border_radius=2)
        pygame.draw.rect(screen, (65, 48, 35), icon_r, 1, border_radius=2)
        screen.blit(font_md.render("Slot vazio",      True, (90, 75, 55)),  (icon_r.right + _PAD, cy))
        screen.blit(font_sm.render("(nada equipado)", True, (75, 60, 45)),
                    (icon_r.right + _PAD, cy + font_md.get_height() + 2))

    cy += _ICON_S + _PAD

    # Divider
    pygame.draw.line(screen, (80, 60, 35), (x + _PAD, cy), (x + _W - _PAD, cy))
    cy += 1 + _PAD

    # Weapon type header row (One/Two Hand  +  subtype)
    if show_weapon_hdr:
        if equipped_item and equipped_item.item_type == "weapon":
            hand = "Two Hand" if equipped_item.two_handed else "One Hand"
            sub  = equipped_item.subtype if equipped_item.subtype else "Weapon"
            screen.blit(font_sm.render(hand, True, (160, 130, 80)), (x + _PAD, cy))
            sub_s = font_sm.render(sub, True, (200, 200, 200))
            screen.blit(sub_s, (x + _W - _PAD - sub_s.get_width(), cy))
        cy += _ROW_H

    # Attribute rows
    VAL_X  = x + _PAD + 110
    DIFF_X = x + _PAD + 165

    if not all_attrs:
        screen.blit(font_sm.render("(sem atributos)", True, (100, 90, 75)), (x + _PAD, cy))
        return

    for attr in all_attrs:
        eq_val  = eq_mods.get(attr, 0.0)
        new_val = new_mods.get(attr, 0.0)

        if attr == "__dano":
            short   = "Dano"
            if equipped_item and equipped_item.damage_min > 0:
                val_str = f"{equipped_item.damage_min}-{equipped_item.damage_max}"
            else:
                val_str = "-"
            val_col = (200, 180, 100)
            diff    = new_val - eq_val
            if diff > 0.5:
                d_str, d_col = f"+{diff:.0f} ▲", (80, 220, 80)
            elif diff < -0.5:
                d_str, d_col = f"{diff:.0f} ▼", (220, 80, 80)
            else:
                d_str, d_col = "=", (140, 140, 140)

        elif attr == "__speed":
            short   = "Velocidade"
            val_str = f"{eq_val:.1f}s" if eq_val > 0 else "-"
            val_col = (200, 180, 100)
            # Lower speed = better; invert diff direction
            diff    = eq_val - new_val
            if diff > 0.05:
                d_str, d_col = "mais rápido ▲", (80, 220, 80)
            elif diff < -0.05:
                d_str, d_col = "mais lento ▼",  (220, 80, 80)
            else:
                d_str, d_col = "=", (140, 140, 140)

        else:
            short   = _ATTR_LABELS.get(attr, attr.replace("_", " ")[:14])
            val_str = f"+{eq_val:.0f}" if eq_val > 0 else (f"{eq_val:.0f}" if eq_val != 0 else "0")
            val_col = (130, 200, 130) if eq_val > 0 else (180, 170, 155)
            diff    = new_val - eq_val
            if diff > 0:
                d_str, d_col = f"+{diff:.0f} ▲", (80, 220, 80)
            elif diff < 0:
                d_str, d_col = f"{diff:.0f} ▼", (220, 80, 80)
            else:
                d_str, d_col = "=", (140, 140, 140)

        screen.blit(font_sm.render(short,   True, (165, 155, 140)), (x + _PAD, cy))
        screen.blit(font_sm.render(val_str, True, val_col),          (VAL_X,   cy))
        screen.blit(font_sm.render(d_str,   True, d_col),            (DIFF_X,  cy))
        cy += _ROW_H
