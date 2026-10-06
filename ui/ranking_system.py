"""
ranking_system.py — Modal de Ranking Geral (06/10/2026, pedido do usuário,
com esboço fornecido: coluna de categorias à esquerda, tabela rolável à
direita). Lista Nível + as 11 trilhas de Skill Level (SKILL_IDS,
engine/components.py) de TODOS os personagens já criados — online ou não
(decisão do usuário: "ranking de verdade").

Read-only, sem predição local nenhuma — pede os dados ao servidor
(RANKING_REQUEST/RANKING_RESULT, shared/messages.py) e só exibe o que
chega. Resultado de cada categoria fica cacheado em `self._data` pela
duração da sessão (nunca re-pede a MESMA categoria clicada de novo) —
ranking não muda a cada frame, diferente de posição de entidade.
"""
from __future__ import annotations
import pygame
from ui.ui_helpers import fill_surf
from engine.components import SKILL_IDS
from ui.skill_level_ui import SKILL_LABELS
from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI

PANEL_W     = UI.RANKING_W
PANEL_H     = UI.RANKING_H
SIDEBAR_W   = UI.RANKING_SIDEBAR_W
PAD         = 16
HEADER_H    = 56
ROW_H       = 28
SIDEBAR_ROW_H = 32
SCROLLBAR_W = 10

C_BG        = (14, 10, 6, 230)
C_BORDER    = (100, 80, 50)
C_TITLE     = (220, 190, 120)
C_WHITE     = (230, 230, 230)
C_GRAY      = (140, 130, 110)
C_GOLD      = (255, 210, 60)
C_SIDEBAR_BG     = (20, 16, 10)
C_SIDEBAR_SEL    = (60, 48, 24)
C_SIDEBAR_HOVER  = (40, 34, 20)
C_ROW_ALT        = (22, 18, 12)
C_TRACK          = (30, 26, 18)
C_THUMB          = (130, 110, 70)

# Ordem de exibição: Level primeiro, depois SKILL_IDS na ordem do catálogo
# (mesma ordem já usada pelo painel de Skill Level, ui/skill_level_ui.py).
_CATEGORIES: "list[tuple[str, str]]" = [("level", "Rank Level")] + [
    (sid, f"Rank {SKILL_LABELS[sid]}") for sid in SKILL_IDS
]


class RankingSystem(UIScaleMixin):
    _FONT_BASES = {"font_sm": 18, "font_md": 22, "font_lg": 28}

    def __init__(self, world, player_entity_id: int, screen: pygame.Surface):
        super().__init__()
        self.world       = world
        self.player_id   = player_entity_id
        self.screen      = screen
        self.wants_close: bool = False
        self._net = None   # setado de fora (game.py), mesmo padrão de TrainerSystem/ShopSystem

        self._category: str = "level"
        self._data: "dict[str, list]" = {}
        self._pending: set = set()   # categorias com RANKING_REQUEST em voo
        self._scroll: int = 0

    # ------------------------------------------------------------------
    def on_open(self) -> None:
        """Chamado por game.py sempre que o modal abre (tecla K) — garante
        que a categoria atual já tem (ou já pediu) dado, sem re-pedir
        categorias já cacheadas de uma abertura anterior."""
        self._request_category(self._category)

    def _request_category(self, category: str) -> None:
        self._category = category
        self._scroll    = 0
        if category in self._data or category in self._pending:
            return
        if self._net is None:
            return
        self._pending.add(category)
        from shared.messages import MsgType
        self._net.send(MsgType.RANKING_REQUEST, {"category": category})

    def on_result(self, category: str, entries: list) -> None:
        """Chamado por client/network_handlers.py::_handle_msg_ranking_result."""
        self._pending.discard(category)
        self._data[category] = entries

    # ------------------------------------------------------------------
    def _panel_rect(self) -> pygame.Rect:
        x0, y0 = self._safe_panel_origin(PANEL_W, PANEL_H)
        x0, y0 = x0 + UI.RANKING_OFFSET_X, y0 + UI.RANKING_OFFSET_Y
        return pygame.Rect(x0, y0, self._u(PANEL_W), self._u(PANEL_H))

    def _close_btn_rect(self, panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.right - self._u(46), panel.y + self._u(8),
                           self._u(38), self._u(38))

    def _sidebar_rect(self, panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.x + self._u(PAD), panel.y + self._u(HEADER_H),
                           self._u(SIDEBAR_W), panel.h - self._u(HEADER_H) - self._u(PAD))

    def _table_rect(self, panel: pygame.Rect) -> pygame.Rect:
        sb = self._sidebar_rect(panel)
        tx = sb.right + self._u(PAD)
        return pygame.Rect(tx, panel.y + self._u(HEADER_H),
                           panel.right - self._u(PAD) - tx, sb.h)

    def _visible_rows(self, table: pygame.Rect) -> int:
        return max(1, (table.h - self._u(28)) // self._u(ROW_H))

    # ------------------------------------------------------------------
    def handle_events(self, events: list, panel: pygame.Rect) -> None:
        self.wants_close = False
        sidebar = self._sidebar_rect(panel)
        table   = self._table_rect(panel)
        entries = self._data.get(self._category, [])
        visible = self._visible_rows(table)
        max_scroll = max(0, len(entries) - visible)

        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self._close_btn_rect(panel).collidepoint(event.pos):
                    self.wants_close = True
                    return
                if sidebar.collidepoint(event.pos):
                    idx = (event.pos[1] - sidebar.y) // self._u(SIDEBAR_ROW_H)
                    if 0 <= idx < len(_CATEGORIES):
                        self._request_category(_CATEGORIES[idx][0])
            elif event.type == pygame.MOUSEWHEEL:
                if table.collidepoint(pygame.mouse.get_pos()):
                    self._scroll = max(0, min(max_scroll, self._scroll - event.y))

        self._scroll = max(0, min(max_scroll, self._scroll))

    # ------------------------------------------------------------------
    def render(self) -> None:
        panel = self._panel_rect()
        mx, my = pygame.mouse.get_pos()

        self.screen.blit(fill_surf(self.screen.get_size(), (0, 0, 0, 160)), (0, 0))
        self.screen.blit(fill_surf((panel.w, panel.h), C_BG), panel.topleft)
        pygame.draw.rect(self.screen, C_BORDER, panel, 2, border_radius=6)

        title = self.font_lg.render("Ranking Geral", False, C_TITLE)
        self.screen.blit(title, (panel.x + (panel.w - title.get_width()) // 2,
                                 panel.y + self._u(10)))
        pygame.draw.line(self.screen, C_BORDER,
                         (panel.x + self._u(4), panel.y + self._u(HEADER_H) - self._u(6)),
                         (panel.right - self._u(4), panel.y + self._u(HEADER_H) - self._u(6)))

        self._render_sidebar(panel, mx, my)
        self._render_table(panel, mx, my)

        close_r = self._close_btn_rect(panel)
        pygame.draw.rect(self.screen,
                         (180, 60, 60) if close_r.collidepoint(mx, my) else (100, 35, 35),
                         close_r, border_radius=3)
        xs = self.font_md.render("X", False, (255, 255, 255))
        self.screen.blit(xs, xs.get_rect(center=close_r.center))

    def _render_sidebar(self, panel: pygame.Rect, mx: int, my: int) -> None:
        sidebar = self._sidebar_rect(panel)
        self.screen.blit(fill_surf(sidebar.size, C_SIDEBAR_BG), sidebar.topleft)
        row_h = self._u(SIDEBAR_ROW_H)
        for i, (cat_id, label) in enumerate(_CATEGORIES):
            r = pygame.Rect(sidebar.x, sidebar.y + i * row_h, sidebar.w, row_h)
            if r.bottom > sidebar.bottom:
                break
            selected = cat_id == self._category
            hov      = r.collidepoint(mx, my)
            if selected:
                pygame.draw.rect(self.screen, C_SIDEBAR_SEL, r)
            elif hov:
                pygame.draw.rect(self.screen, C_SIDEBAR_HOVER, r)
            color = C_GOLD if selected else C_WHITE
            s = self.font_sm.render(label, False, color)
            self.screen.blit(s, (r.x + self._u(8), r.centery - s.get_height() // 2))

    def _render_table(self, panel: pygame.Rect, mx: int, my: int) -> None:
        table = self._table_rect(panel)
        col_name_w = int(table.w * 0.45)
        col_rank_w = int(table.w * 0.20)

        hy = table.y
        self.screen.blit(self.font_sm.render("NOME", False, C_GRAY), (table.x, hy))
        self.screen.blit(self.font_sm.render("RANK", False, C_GRAY), (table.x + col_name_w, hy))
        label = "LEVEL" if self._category == "level" else "SKILL LEVEL"
        self.screen.blit(self.font_sm.render(label, False, C_GRAY),
                         (table.x + col_name_w + col_rank_w, hy))
        header_h = self._u(28)
        pygame.draw.line(self.screen, C_BORDER,
                         (table.x, table.y + header_h - self._u(4)),
                         (table.right, table.y + header_h - self._u(4)))

        entries = self._data.get(self._category, [])
        list_y0 = table.y + header_h
        visible = self._visible_rows(table)
        row_h   = self._u(ROW_H)

        if self._category in self._pending and not entries:
            wait_s = self.font_sm.render("Carregando...", False, C_GRAY)
            self.screen.blit(wait_s, (table.x, list_y0))
            return
        if not entries:
            empty_s = self.font_sm.render("Sem dados ainda.", False, C_GRAY)
            self.screen.blit(empty_s, (table.x, list_y0))
            return

        shown = entries[self._scroll:self._scroll + visible]
        for i, entry in enumerate(shown):
            rank = self._scroll + i + 1
            ry = list_y0 + i * row_h
            if i % 2 == 0:
                self.screen.blit(fill_surf((table.w - self._u(SCROLLBAR_W) - self._u(4), row_h),
                                           C_ROW_ALT), (table.x, ry))
            name_s = self.font_sm.render(entry.get("name", "?"), False, C_WHITE)
            self.screen.blit(name_s, (table.x, ry + (row_h - name_s.get_height()) // 2))
            rank_s = self.font_sm.render(f"#{rank}", False, C_GOLD)
            self.screen.blit(rank_s, (table.x + col_name_w, ry + (row_h - rank_s.get_height()) // 2))
            value_s = self.font_sm.render(str(entry.get("value", 0)), False, C_WHITE)
            self.screen.blit(value_s, (table.x + col_name_w + col_rank_w,
                                       ry + (row_h - value_s.get_height()) // 2))

        if len(entries) > visible:
            track = pygame.Rect(table.right - self._u(SCROLLBAR_W), list_y0,
                                self._u(SCROLLBAR_W), table.bottom - list_y0)
            pygame.draw.rect(self.screen, C_TRACK, track, border_radius=3)
            max_scroll = max(1, len(entries) - visible)
            thumb_h = max(self._u(16), track.h * visible // len(entries))
            thumb_y = track.y + (track.h - thumb_h) * self._scroll // max_scroll
            pygame.draw.rect(self.screen, C_THUMB,
                             (track.x, thumb_y, track.w, thumb_h), border_radius=3)
