"""
skill_level_ui.py — Painel read-only de Skill Level (Tibia-like).

Lista as 11 trilhas (armas, escudo, defesa, resistências, magic) com level
atual, barra de progresso de xp e bônus % corrente. Sem interação — não há
alocação manual, o servidor concede xp por uso (ver stats_system.py).
"""
from __future__ import annotations
import pygame
from components import SkillLevels, CharacterStats, SKILL_IDS, MAX_SKILL_LEVEL
from stats_system import skill_xp_for_level, skill_bonus_pct
from ui_scale_mixin import UIScaleMixin
from ui_sizes import UI

PANEL_W      = UI.SKILL_LEVELS_W
PANEL_H      = UI.SKILL_LEVELS_H
PAD          = UI.SKILL_LEVELS_PAD
HEADER_H     = UI.SKILL_LEVELS_HEADER_H
CHAR_ROW_H   = UI.SKILL_LEVELS_CHAR_ROW_H
ROW_H        = UI.SKILL_LEVELS_ROW_H
BAR_W        = UI.SKILL_LEVELS_BAR_W
BAR_H        = UI.SKILL_LEVELS_BAR_H
NAME_COL_W   = UI.SKILL_LEVELS_NAME_COL_W

C_BG       = (14, 10, 6, 230)
C_BORDER   = (100, 80, 50)
C_TITLE    = (220, 190, 120)
C_WHITE    = (230, 230, 230)
C_GRAY     = (140, 130, 110)
C_GOLD     = (255, 210, 60)
C_BAR_BG   = (40, 34, 24)
C_BAR_FILL = (90, 150, 90)
C_BAR_MAX  = (255, 200, 50)
C_XP_BG    = (0, 40, 80)
C_XP_FILL  = (0, 220, 220)   # mesmo C_CYAN que o HUD usava antes de mover a barra pra aqui
C_BAR_TEXT = (235, 235, 235)  # texto sobreposto na barra (xp atual/necessário, ou MAX)

# Ordem de exibição = SKILL_IDS (components.py). Rótulo amigável por skill_id.
SKILL_LABELS: dict[str, str] = {
    "machado":         "Machado",
    "espada":          "Espada",
    "maca":            "Maça",
    "arco":            "Arco",
    "baculo":          "Báculo",
    "escudo":          "Escudo",
    "defesa":          "Defesa",
    "resist_fogo":     "Resistência a Fogo",
    "resist_gelo":     "Resistência a Gelo",
    "resist_natureza": "Resistência a Natureza",
    "magic":           "Magic",
}


class SkillLevelUI(UIScaleMixin):
    """Painel read-only — lista as 11 trilhas de skill level do jogador."""

    _FONT_BASES = {
        "font_lg": 34,
        "font_md": 27,
        "font_sm": 22,
    }

    def __init__(self, world, player_entity_id: int, screen: pygame.Surface):
        super().__init__()
        self.world     = world
        self.player_id = player_entity_id
        self.screen    = screen
        self.wants_close: bool = False

    def _skill_levels(self) -> "SkillLevels | None":
        return self.world.get_component(self.player_id, SkillLevels)

    def _panel_rect(self) -> pygame.Rect:
        x0, y0 = self._safe_panel_origin(PANEL_W, PANEL_H)
        x0, y0 = x0 + UI.SKILL_LEVELS_OFFSET_X, y0 + UI.SKILL_LEVELS_OFFSET_Y
        return pygame.Rect(x0, y0, self._u(PANEL_W), self._u(PANEL_H))

    def _close_btn_rect(self, panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.right - self._u(46), panel.y + self._u(8),
                           self._u(38), self._u(38))

    def handle_events(self, events: list, panel: pygame.Rect) -> None:
        self.wants_close = False
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self._close_btn_rect(panel).collidepoint(event.pos):
                    self.wants_close = True
                    return

    def render(self) -> None:
        skl = self._skill_levels()
        if not skl:
            return

        panel = self._panel_rect()

        overlay = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        bg = pygame.Surface((panel.w, panel.h), pygame.SRCALPHA)
        bg.fill(C_BG)
        self.screen.blit(bg, panel.topleft)
        pygame.draw.rect(self.screen, C_BORDER, panel, 2, border_radius=6)

        title = self.font_lg.render("Skill Level", True, C_TITLE)
        self.screen.blit(title, (panel.x + self._u(PAD), panel.y + self._u(8)))

        row_y = panel.y + self._u(HEADER_H)
        char = self.world.get_component(self.player_id, CharacterStats)
        if char:
            self._render_char_level(panel, row_y, char)
        row_y += self._u(CHAR_ROW_H)

        for skill_id in SKILL_IDS:
            self._render_row(panel, row_y, skill_id, skl)
            row_y += self._u(ROW_H)

        close_r = self._close_btn_rect(panel)
        mx, my  = pygame.mouse.get_pos()
        pygame.draw.rect(self.screen,
                         (180, 60, 60) if close_r.collidepoint(mx, my) else (100, 35, 35),
                         close_r, border_radius=3)
        xs = self.font_md.render("X", True, (255, 255, 255))
        self.screen.blit(xs, xs.get_rect(center=close_r.center))

    def _render_char_level(self, panel: pygame.Rect, row_y: int,
                           char: CharacterStats) -> None:
        """Nível + XP do personagem, em 1 linha (nome + barra com xp sobreposto)
        — removido do HUD permanente por pedido do usuário, mostrado só aqui
        por enquanto (ver client/hud_handlers.py)."""
        x = panel.x + self._u(PAD)
        name_surf = self.font_md.render(f"Nível {char.level}", True, C_WHITE)
        name_y = row_y + (self._u(CHAR_ROW_H) - self._u(8) - name_surf.get_height()) // 2
        self.screen.blit(name_surf, (x, name_y))

        bar_x = panel.right - self._u(PAD) - self._u(BAR_W)
        bar_y = row_y + (self._u(CHAR_ROW_H) - self._u(8) - self._u(BAR_H)) // 2
        bar_rect = pygame.Rect(bar_x, bar_y, self._u(BAR_W), self._u(BAR_H))
        pygame.draw.rect(self.screen, C_XP_BG, bar_rect, border_radius=3)
        needed = max(1, char.xp_to_next_level)
        frac   = max(0.0, min(1.0, char.current_xp / needed))
        fill_w = max(1, int(bar_rect.w * frac))
        pygame.draw.rect(self.screen, C_XP_FILL,
                         (bar_rect.x, bar_rect.y, fill_w, bar_rect.h), border_radius=3)
        pygame.draw.rect(self.screen, C_BORDER, bar_rect, 1, border_radius=3)

        xp_surf = self.font_sm.render(f"{char.current_xp}/{char.xp_to_next_level}",
                                      True, C_BAR_TEXT)
        self.screen.blit(xp_surf, xp_surf.get_rect(center=bar_rect.center))

        sep_y = row_y + self._u(CHAR_ROW_H) - self._u(6)
        pygame.draw.line(self.screen, C_BORDER,
                         (panel.x + self._u(PAD), sep_y),
                         (panel.right - self._u(PAD), sep_y), 1)

    def _render_row(self, panel: pygame.Rect, row_y: int, skill_id: str,
                    skl: SkillLevels) -> None:
        """1 linha por skill: nome + 'Lv X (+Y%)' lado a lado, barra de
        progresso à direita com o xp atual/necessário sobreposto e centrado."""
        level = skl.levels[skill_id]
        xp    = skl.xp[skill_id]
        label = SKILL_LABELS.get(skill_id, skill_id)
        bonus = skill_bonus_pct(level)

        x = panel.x + self._u(PAD)
        name_surf = self.font_md.render(label, True, C_WHITE)
        name_y = row_y + (self._u(ROW_H) - name_surf.get_height()) // 2
        self.screen.blit(name_surf, (x, name_y))

        lvl_color = C_GOLD if level >= MAX_SKILL_LEVEL else C_WHITE
        lvl_surf  = self.font_sm.render(f"Lv {level} (+{bonus * 100:.1f}%)", True, lvl_color)
        lvl_x = panel.x + self._u(PAD) + self._u(NAME_COL_W)
        lvl_y = row_y + (self._u(ROW_H) - lvl_surf.get_height()) // 2
        self.screen.blit(lvl_surf, (lvl_x, lvl_y))

        bar_x = panel.right - self._u(PAD) - self._u(BAR_W)
        bar_y = row_y + (self._u(ROW_H) - self._u(BAR_H)) // 2
        bar_rect = pygame.Rect(bar_x, bar_y, self._u(BAR_W), self._u(BAR_H))
        pygame.draw.rect(self.screen, C_BAR_BG, bar_rect, border_radius=3)

        if level >= MAX_SKILL_LEVEL:
            pygame.draw.rect(self.screen, C_BAR_MAX, bar_rect, border_radius=3)
            pct_text = "MAX"
        else:
            needed = skill_xp_for_level(level)
            frac   = max(0.0, min(1.0, xp / needed)) if needed > 0 else 0.0
            fill_w = max(1, int(bar_rect.w * frac))
            fill_rect = pygame.Rect(bar_rect.x, bar_rect.y, fill_w, bar_rect.h)
            pygame.draw.rect(self.screen, C_BAR_FILL, fill_rect, border_radius=3)
            pct_text = f"{xp}/{needed}"
        pygame.draw.rect(self.screen, C_BORDER, bar_rect, 1, border_radius=3)

        pct_surf = self.font_sm.render(pct_text, True, C_BAR_TEXT)
        self.screen.blit(pct_surf, pct_surf.get_rect(center=bar_rect.center))
