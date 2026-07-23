"""
char_stats_ui.py — Modal read-only de estatísticas do personagem (Fase E,
23/07/2026).

Diferente de SkillLevelUI (lê um componente ECS sempre sincronizado), os
dados aqui vêm do servidor SOB DEMANDA (CHAR_STATS_REQUEST/CHAR_STATS_DATA,
ver shared/messages.py) — CharStatsTracker (engine/components.py) é
server-autoritativo e não é empurrado a cada tick (mudaria raramente e o
modal só abre ocasionalmente). `set_data()` é chamado pelo handler de rede
(client/network_handlers.py) quando a resposta chega.
"""
from __future__ import annotations
import pygame
from ui.ui_helpers import fill_surf
from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI

PANEL_W  = UI.CHAR_STATS_W
PANEL_H  = UI.CHAR_STATS_H
PAD      = UI.CHAR_STATS_PAD
HEADER_H = UI.CHAR_STATS_HEADER_H
ROW_H    = UI.CHAR_STATS_ROW_H

C_BG     = (14, 10, 6, 230)
C_BORDER = (100, 80, 50)
C_TITLE  = (220, 190, 120)
C_WHITE  = (230, 230, 230)
C_GRAY   = (140, 130, 110)
C_GOLD   = (255, 210, 60)

_ROWS = [
    ("Dano causado (PvE)", "pve_damage"),
    ("Dano causado (PvP)", "pvp_damage"),
    ("Mobs mortos",        "mobs_killed"),
    ("Players mortos",     "players_killed"),
    ("Quests concluídas",  "quests_completed"),
    ("Duelos vencidos",    "duel_wins"),
    ("Duelos perdidos",    "duel_losses"),
]
# (mode_id, rótulo) — mode_id bate com a chave em arena_wins/arena_losses
# (CharStatsTracker.arena_wins/losses, engine/components.py). "1v1" já
# existe no schema desde a Fase E mesmo sem o modo estar implementado
# ainda (chega na Fase H) — nunca vai ter vitória/derrota até lá, exibe 0.
_ARENA_MODES = [("1v1", "Duelo (Arena)"), ("2v2", "Arena 2x2"), ("3v3", "Arena 3x3")]


class CharStatsUI(UIScaleMixin):
    """Painel read-only — snapshot de CharStatsTracker pedido sob demanda."""

    _FONT_BASES = {"font_lg": 30, "font_md": 24, "font_sm": 20}

    def __init__(self, screen: pygame.Surface):
        super().__init__()
        self.screen = screen
        self.wants_close: bool = False
        self._data: "dict | None" = None
        # Setado pelo game.py — dispara CHAR_STATS_REQUEST ao abrir.
        self.on_open: "callable | None" = None

    def set_data(self, data: dict) -> None:
        self._data = data

    def open(self) -> None:
        self._data = None   # mostra "Carregando..." até a resposta chegar
        if self.on_open:
            self.on_open()

    def _panel_rect(self) -> pygame.Rect:
        x0, y0 = self._safe_panel_origin(PANEL_W, PANEL_H)
        x0, y0 = x0 + UI.CHAR_STATS_OFFSET_X, y0 + UI.CHAR_STATS_OFFSET_Y
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
        panel = self._panel_rect()

        self.screen.blit(fill_surf(self.screen.get_size(), (0, 0, 0, 160)), (0, 0))
        self.screen.blit(fill_surf((panel.w, panel.h), C_BG), panel.topleft)
        pygame.draw.rect(self.screen, C_BORDER, panel, 2, border_radius=6)

        title = self.font_lg.render("Estatísticas", False, C_TITLE)
        self.screen.blit(title, (panel.x + self._u(PAD), panel.y + self._u(8)))

        close_r = self._close_btn_rect(panel)
        mx, my  = pygame.mouse.get_pos()
        pygame.draw.rect(self.screen,
                         (180, 60, 60) if close_r.collidepoint(mx, my) else (100, 35, 35),
                         close_r, border_radius=3)
        xs = self.font_md.render("X", False, (255, 255, 255))
        self.screen.blit(xs, xs.get_rect(center=close_r.center))

        if self._data is None:
            wait_surf = self.font_md.render("Carregando...", False, C_GRAY)
            self.screen.blit(wait_surf, wait_surf.get_rect(center=panel.center))
            return

        row_y = panel.y + self._u(HEADER_H)
        for label, key in _ROWS:
            self._render_row(panel, row_y, label, str(self._data.get(key, 0)))
            row_y += self._u(ROW_H)

        row_y += self._u(10)
        pygame.draw.line(self.screen, C_BORDER,
                         (panel.x + self._u(PAD), row_y),
                         (panel.right - self._u(PAD), row_y), 1)
        row_y += self._u(14)
        sep_surf = self.font_sm.render("Arenas (vitórias / derrotas)", False, C_GOLD)
        self.screen.blit(sep_surf, (panel.x + self._u(PAD), row_y))
        row_y += self._u(ROW_H)

        wins   = self._data.get("arena_wins", {}) or {}
        losses = self._data.get("arena_losses", {}) or {}
        for mode_id, mode_label in _ARENA_MODES:
            value = f"{wins.get(mode_id, 0)} / {losses.get(mode_id, 0)}"
            self._render_row(panel, row_y, mode_label, value)
            row_y += self._u(ROW_H)

    def _render_row(self, panel: pygame.Rect, row_y: int, label: str, value: str) -> None:
        x = panel.x + self._u(PAD)
        name_surf = self.font_md.render(label, False, C_WHITE)
        self.screen.blit(name_surf, (x, row_y))

        val_surf = self.font_md.render(value, False, C_GOLD)
        val_x = panel.right - self._u(PAD) - val_surf.get_width()
        self.screen.blit(val_surf, (val_x, row_y))
