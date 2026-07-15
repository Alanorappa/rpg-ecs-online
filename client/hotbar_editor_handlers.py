"""
hotbar_editor_handlers.py — Mixin com o editor de atalhos da hotbar
(tecla K): tabela de rebind de teclas (menus, slots de habilidade e
consumíveis) com captura de tecla e botões Salvar/Fechar. Inclui as
constantes de geometria _HBE_*. Separado de game.py para manter
GameEngine conciso. Esta classe NÃO deve ser instanciada diretamente —
ela é herdada por GameEngine, que fornece self.world, self.player_entity,
self.screen, self.font_*, self._menu_keys, self._mkb_rebind e os demais
atributos referenciados aqui.
"""
import pygame
from ui.ui_helpers import fill_surf

from engine.components import PlayerSkills
from ui.ui_sizes import UI


class HotbarEditorHandlers:
    # ------------------------------------------------------------------
    # Editor da hotbar (K)
    # ------------------------------------------------------------------

    _HBE_SZ  = UI.HOTBAR_EDITOR_SLOT_SZ
    _HBE_GAP = UI.HOTBAR_EDITOR_GAP

    def _close_hotbar_editor(self) -> None:
        self._show_hotbar_editor    = False
        self._hbe_drag_from         = None
        self._hbe_rebind_slot       = None
        self._hbe_cons_drag_from    = None
        self._hbe_cons_rebind_slot  = None
        self._hbe_tab               = 0
        self._mkb_rebind            = None
        self._mkb_scroll            = 0
        self._hbe_skill_scroll      = 0
        self._hbe_expand_slots      = False
        self._save_config()

    def _draw_hotbar_editor(self, events: list) -> None:
        """Painel 'Atalhos do teclado' — tabela de rebind + Salvar / Fechar."""
        from content.skill_config import SKILL_CATALOG, NUM_SLOTS
        from engine.components import ConsumableBar as _CB

        ps   = self.world.get_component(self.player_entity, PlayerSkills)
        cbar = self.world.get_component(self.player_entity, _CB)
        if not ps:
            return

        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)

        # ── Captura de tecla para rebind ──────────────────────────────────
        for e in events:
            if e.type != pygame.KEYDOWN:
                continue
            if self._mkb_rebind is not None:
                if e.key == pygame.K_ESCAPE:
                    self._mkb_rebind = None
                else:
                    action, idx = self._mkb_rebind.split(":", 1)
                    if action == "menu":
                        self._menu_keys[idx] = e.key
                    elif action == "slot":
                        ps.keybinds[int(idx)] = e.key
                    elif action == "cons" and cbar:
                        cbar.keybinds[int(idx)] = e.key
                    self._mkb_rebind = None
            elif e.key == pygame.K_ESCAPE:
                self._close_hotbar_editor()
                return

        # ── Geometria ─────────────────────────────────────────────────────
        # Altura do painel agora é um TETO fixo (HOTBAR_EDITOR_MAX_H) — antes
        # crescia com o nº de linhas (menus+slots+consumíveis) sem limite,
        # transbordando a tela quando havia muitos slots (bug relatado pelo
        # usuário 13/07/2026, print mostrando "Slot 15" cortado no rodapé e
        # os botões Salvar/Fechar soltos no meio do conteúdo). A área de
        # linhas agora é uma viewport com clip + scroll (self._mkb_scroll,
        # px) — mesmo tratamento aplicado ao diálogo de quest (ver
        # ui/quest_system.py::_blit_scrollable).
        _MENU_ROWS = [("Inventário", "inventario"), ("Talentos", "talentos"),
                      ("Mapa", "mapa"), ("Diário de Quests", "diario"),
                      ("Habilidades", "habilidades")]
        n_rows   = len(_MENU_ROWS) + NUM_SLOTS + _CB.NUM_SLOTS
        ppx, ppy = self._safe_panel_origin(UI.HOTBAR_EDITOR_W, UI.HOTBAR_EDITOR_MAX_H)
        ppx, ppy = ppx + UI.HOTBAR_EDITOR_OFFSET_X, ppy + UI.HOTBAR_EDITOR_OFFSET_Y

        PW  = self._u(UI.HOTBAR_EDITOR_W)
        PH  = self._u(UI.HOTBAR_EDITOR_MAX_H)
        ROW_H  = self._u(38)
        SEC_H  = self._u(24)   # altura de um cabeçalho de seção (label + linha + respiro)
        KEY_W  = self._u(90)
        KEY_H  = self._u(28)
        BTN_W  = self._u(110)
        BTN_H  = self._u(34)

        COL_NAME = ppx + self._u(20)
        COL_KEY  = ppx + PW - KEY_W - self._u(20)

        # Overlay
        self.screen.blit(fill_surf((self.screen.get_width(), self.screen.get_height()), (0, 0, 0, 170)), (0, 0))

        # Painel
        pygame.draw.rect(self.screen, (28, 22, 12), (ppx, ppy, PW, PH), border_radius=8)
        pygame.draw.rect(self.screen, (90, 72, 44), (ppx, ppy, PW, PH), 2, border_radius=8)

        # Título
        title_s = self.font_md.render("Atalhos do teclado", False, (220, 190, 110))
        self.screen.blit(title_s, (ppx + PW // 2 - title_s.get_width() // 2, ppy + self._u(12)))

        # ── Cabeçalho de colunas ──────────────────────────────────────────
        hdr_y = ppy + self._u(42)
        self.screen.blit(self.font_sm.render("Ação", False, (150, 135, 85)),
                         (COL_NAME, hdr_y))
        self.screen.blit(self.font_sm.render("Tecla", False, (150, 135, 85)),
                         (COL_KEY + KEY_W // 2 - self._u(22), hdr_y))
        hdr_y += self._u(20)
        pygame.draw.line(self.screen, (72, 58, 32), (ppx + self._u(12), hdr_y), (ppx + PW - self._u(12), hdr_y))
        hdr_y += self._u(6)

        # ── Viewport rolável das linhas (entre o cabeçalho e os botões) ────
        BTN_ZONE_H  = BTN_H + self._u(58)   # botões + respiro + dica abaixo das linhas
        rows_top    = hdr_y
        rows_bottom = ppy + PH - BTN_ZONE_H
        view_h      = max(0, rows_bottom - rows_top)

        content_h  = 3 * SEC_H + n_rows * ROW_H
        max_scroll = max(0, content_h - view_h)
        self._mkb_scroll = max(0, min(self._mkb_scroll, max_scroll))

        # Mouse wheel — rola a lista (só quando o mouse está sobre o painel)
        panel_r = pygame.Rect(ppx, ppy, PW, PH)
        for e in events:
            if e.type == pygame.MOUSEWHEEL and panel_r.collidepoint(mx, my):
                self._mkb_scroll = max(0, min(max_scroll, self._mkb_scroll - e.y * self._u(30)))

        prev_clip = self.screen.get_clip()
        self.screen.set_clip(pygame.Rect(ppx, rows_top, PW, view_h))
        cy = rows_top - self._mkb_scroll

        def draw_section(label, color=(185, 158, 80)):
            nonlocal cy
            s = self.font_sm.render(label, False, color)
            self.screen.blit(s, (COL_NAME, cy))
            cy += self._u(20)
            pygame.draw.line(self.screen, (60, 48, 28),
                             (ppx + self._u(12), cy), (ppx + PW - self._u(12), cy))
            cy += self._u(4)

        def draw_row(row_label, key_code, key_id):
            nonlocal cy
            # Linha fora da viewport (rolada pra fora) não recebe hover/clique —
            # sem isso, um clique "invisível" (linha escondida atrás do clip,
            # mas o rect ainda existia em coords de tela) podia disparar rebind
            # de uma linha que nem estava sendo mostrada.
            visible = rows_top - ROW_H < cy < rows_bottom
            if not visible:
                cy += ROW_H
                return

            alt = ((cy - rows_top + self._mkb_scroll) // ROW_H) % 2 == 1
            if alt:
                pygame.draw.rect(self.screen, (34, 28, 16),
                                 (ppx + self._u(10), cy, PW - self._u(20), ROW_H - self._u(2)), border_radius=2)
            name_s = self.font_sm.render(row_label, False, (205, 192, 150))
            self.screen.blit(name_s, (COL_NAME, cy + (ROW_H - name_s.get_height()) // 2))

            waiting  = (self._mkb_rebind == key_id)
            key_name = pygame.key.name(key_code).upper() if key_code else "—"
            kr       = pygame.Rect(COL_KEY, cy + (ROW_H - KEY_H) // 2, KEY_W, KEY_H)
            row_fully_visible = rows_top <= cy and (cy + ROW_H) <= rows_bottom
            hov      = row_fully_visible and kr.collidepoint(mx, my)

            if waiting:
                bg, bd, kt, kc = (72,56,18), (225,185,62), "...", (255,225,82)
            elif hov:
                bg, bd, kt, kc = (52,44,22), (165,135,62), key_name, (240,215,135)
            else:
                bg, bd, kt, kc = (38,30,14), (82,67,40), key_name, (175,155,92)

            pygame.draw.rect(self.screen, bg, kr, border_radius=4)
            pygame.draw.rect(self.screen, bd, kr, 1, border_radius=4)
            ks = self.font_sm.render(kt, False, kc)
            self.screen.blit(ks, ks.get_rect(center=kr.center))
            if clicked and hov and not waiting:
                self._mkb_rebind = key_id
            cy += ROW_H

        # ── Menus ─────────────────────────────────────────────────────────
        draw_section("Menus")
        for label, mid in _MENU_ROWS:
            draw_row(label, self._menu_keys.get(mid, 0), f"menu:{mid}")

        # ── Barra de Habilidades ──────────────────────────────────────────
        draw_section("Barra de Habilidades")
        for i in range(NUM_SLOTS):
            key_code = ps.keybinds[i] if i < len(ps.keybinds) else 0
            draw_row(f"Slot {i + 1}", key_code, f"slot:{i}")

        # ── Consumíveis ───────────────────────────────────────────────────
        draw_section("Barra de Consumíveis")
        for i in range(_CB.NUM_SLOTS):
            key_code = cbar.keybinds[i] if cbar and i < len(cbar.keybinds) else 0
            draw_row(f"Slot {NUM_SLOTS + i + 1}", key_code, f"cons:{i}")

        self.screen.set_clip(prev_clip)

        # ── Barra de rolagem (só quando o conteúdo não cabe) ───────────────
        if max_scroll > 0 and view_h > 0:
            sb_w    = self._u(5)
            sb_x    = ppx + PW - self._u(10)
            thumb_h = max(self._u(20), int(view_h * view_h / content_h))
            thumb_y = rows_top + int((view_h - thumb_h) * self._mkb_scroll / max_scroll)
            pygame.draw.rect(self.screen, (40, 34, 18), (sb_x, rows_top, sb_w, view_h), border_radius=2)
            pygame.draw.rect(self.screen, (90, 72, 44), (sb_x, thumb_y, sb_w, thumb_h), border_radius=2)

        # ── Botões Salvar / Fechar ────────────────────────────────────────
        btn_y   = ppy + PH - BTN_H - self._u(14)
        btn_gap = self._u(16)
        total_btns_w = 2 * BTN_W + btn_gap
        btn_x0  = ppx + PW // 2 - total_btns_w // 2

        for bi, (blabel, bcolor, bhover) in enumerate([
            ("Salvar",  (38, 72, 38),  (55, 100, 55)),
            ("Fechar",  (60, 30, 20),  (90, 45, 30)),
        ]):
            br   = pygame.Rect(btn_x0 + bi * (BTN_W + btn_gap), btn_y, BTN_W, BTN_H)
            hov  = br.collidepoint(mx, my)
            pygame.draw.rect(self.screen, bhover if hov else bcolor, br, border_radius=6)
            pygame.draw.rect(self.screen, (120, 100, 55), br, 1, border_radius=6)
            bs   = self.font_sm.render(blabel, False, (220, 205, 150))
            self.screen.blit(bs, bs.get_rect(center=br.center))
            if clicked and hov:
                self._save_config()
                if blabel == "Fechar":
                    self._close_hotbar_editor()
                return

        # Dica
        if self._mkb_rebind:
            hint = self.font_xs.render(
                "Pressione a nova tecla  |  ESC para cancelar", False, (200, 180, 80))
        else:
            hint = self.font_xs.render(
                "Clique na tecla para rebindear  |  ESC para fechar", False, (90, 82, 56))
        self.screen.blit(hint, hint.get_rect(centerx=ppx + PW // 2, y=btn_y - self._u(18)))
