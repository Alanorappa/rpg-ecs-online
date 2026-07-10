"""
menu_handlers.py — Mixin com o menu de pausa (ESC) e seus submenus:
tela principal, confirmação de saída, resolução, interface (escala
de UI) e som. Inclui os helpers de estilo reutilizados (_mm_overlay/
_mm_panel/_mm_button) e as constantes de cor _MM_*. Separado de
game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.screen, self.font_md/font_sm, self._scale/_ui_scale e os demais
atributos referenciados aqui.
"""
import pygame
from ui_helpers import fill_surf

from sound_manager import SOUNDS
from ui_sizes import UI


class MenuHandlers:
    # ------------------------------------------------------------------
    # Menu de pausa
    # ------------------------------------------------------------------

    # ── helpers de estilo reutilizados pelos submenus ──────────────────────
    _MM_BG_COL    = (14, 10, 6)
    _MM_BORDER    = (90, 72, 44)
    _MM_TITLE_COL = (220, 190, 110)
    _MM_BTN_HOV   = (80, 62, 28)
    _MM_BTN_NRM   = (38, 30, 16)
    _MM_BTN_TXT   = (230, 210, 160)
    _MM_BORDER_HOV= (200, 160, 60)

    def _mm_overlay(self):
        self.screen.blit(fill_surf((self.screen.get_width(), self.screen.get_height()), (0, 0, 0, 150)), (0, 0))

    def _mm_panel(self, pw, ph, offset=(0, 0)):
        px = self.screen.get_width()  // 2 - pw // 2 + offset[0]
        py = self.screen.get_height() // 2 - ph // 2 + offset[1]
        self.screen.blit(fill_surf((pw, ph), (*self._MM_BG_COL, 240)), (px, py))
        pygame.draw.rect(self.screen, self._MM_BORDER,
                         (px, py, pw, ph), 2, border_radius=8)
        return px, py

    def _mm_button(self, rect, label, hov):
        bg_c = self._MM_BTN_HOV if hov else self._MM_BTN_NRM
        bdr  = self._MM_BORDER_HOV if hov else self._MM_BORDER
        pygame.draw.rect(self.screen, bg_c, rect, border_radius=5)
        pygame.draw.rect(self.screen, bdr,  rect, 1, border_radius=5)
        lbl = self.font_md.render(label, False, self._MM_BTN_TXT)
        self.screen.blit(lbl, lbl.get_rect(center=rect.center))

    # ── Menu principal ─────────────────────────────────────────────────────
    def _draw_pause_menu(self, events: list) -> "str | None":
        """Dispatcher: redireciona para o submenu activo ou desenha o menu principal."""
        if self._pause_submenu == "resolution":
            return self._draw_resolution_submenu(events)
        if self._pause_submenu == "sound":
            return self._draw_sound_submenu(events)
        if self._pause_submenu == "interface":
            return self._draw_interface_submenu(events)
        if self._pause_submenu == "quit_confirm":
            return self._draw_quit_confirm(events)
        return self._draw_main_menu(events)

    def _draw_quit_confirm(self, events: list) -> "str | None":
        self._set_panel_scale(UI.MENU_QUIT_CONFIRM_W, UI.MENU_QUIT_CONFIRM_H)
        PW, PH  = self._u(UI.MENU_QUIT_CONFIRM_W), self._u(UI.MENU_QUIT_CONFIRM_H)
        self._mm_overlay()
        px, py  = self._mm_panel(PW, PH, (UI.MENU_QUIT_CONFIRM_OFFSET_X, UI.MENU_QUIT_CONFIRM_OFFSET_Y))
        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)

        msg  = self.font_md.render("Tem certeza que deseja sair?", False, (210, 190, 150))
        self.screen.blit(msg, msg.get_rect(center=(px + PW // 2, py + self._u(40))))

        btn_w, btn_h = self._u(100), self._u(36)
        gap   = self._u(20)
        total = btn_w * 2 + gap
        bx    = px + PW // 2 - total // 2
        by    = py + PH - btn_h - self._u(20)

        sim_rect = pygame.Rect(bx, by, btn_w, btn_h)
        nao_rect = pygame.Rect(bx + btn_w + gap, by, btn_w, btn_h)

        # "Sim" em vermelho
        hov_sim = sim_rect.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (100, 30, 20) if hov_sim else (70, 20, 15),
                         sim_rect, border_radius=5)
        pygame.draw.rect(self.screen, (200, 60, 40), sim_rect, 1, border_radius=5)
        lbl = self.font_md.render("Sim", False, (255, 140, 120))
        self.screen.blit(lbl, lbl.get_rect(center=sim_rect.center))
        if hov_sim and clicked:
            return "quit"

        # "Não" normal
        self._mm_button(nao_rect, "Nao", nao_rect.collidepoint(mx, my))
        if nao_rect.collidepoint(mx, my) and clicked:
            self._pause_submenu = ""
        return None

    def _draw_main_menu(self, events: list) -> "str | None":
        _BTNS = [
            ("Resume",              "resume"),
            ("Resolution",          "submenu:resolution"),
            ("Sound",               "submenu:sound"),
            ("Interface",           "submenu:interface"),
            ("Atalhos do teclado",  "open_hotbar_editor"),
            ("Voltar ao Spawn",     "unstuck"),
            ("Quit",                "submenu:quit_confirm"),
        ]
        self._set_panel_scale(UI.MENU_MAIN_W, 60 + len(_BTNS) * 50 + 10)
        PW, PH = self._u(UI.MENU_MAIN_W), self._u(60) + len(_BTNS) * self._u(50) + self._u(10)
        self._mm_overlay()
        px, py = self._mm_panel(PW, PH, (UI.MENU_MAIN_OFFSET_X, UI.MENU_MAIN_OFFSET_Y))

        title = self.font_md.render("Main Menu", False, self._MM_TITLE_COL)
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + self._u(16)))

        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        btn_w, btn_h = self._u(200), self._u(38)
        bx = px + PW // 2 - btn_w // 2
        for i, (label, action) in enumerate(_BTNS):
            rect = pygame.Rect(bx, py + self._u(56) + i * (btn_h + self._u(10)), btn_w, btn_h)
            hov  = rect.collidepoint(mx, my)
            self._mm_button(rect, label, hov)
            if hov and clicked:
                if action.startswith("submenu:"):
                    self._pause_submenu = action.split(":")[1]
                    return None
                return action
        return None

    # ── Submenu Resolution ─────────────────────────────────────────────────
    def _draw_resolution_submenu(self, events: list) -> "str | None":
        from settings_screen import SCALE_OPTIONS
        self._set_panel_scale(UI.MENU_RESOLUTION_W, UI.MENU_RESOLUTION_H)
        PW, PH = self._u(UI.MENU_RESOLUTION_W), self._u(UI.MENU_RESOLUTION_H)
        self._mm_overlay()
        px, py = self._mm_panel(PW, PH, (UI.MENU_RESOLUTION_OFFSET_X, UI.MENU_RESOLUTION_OFFSET_Y))

        title = self.font_md.render("Resolution", False, self._MM_TITLE_COL)
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + self._u(14)))

        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        opt_w, opt_h = self._u(290), self._u(36)
        ox = px + PW // 2 - opt_w // 2
        for i, (label, val) in enumerate(SCALE_OPTIONS):
            oy   = py + self._u(52) + i * (opt_h + self._u(8))
            rect = pygame.Rect(ox, oy, opt_w, opt_h)
            hov  = rect.collidepoint(mx, my)
            is_sel = abs(val - self._scale) < 0.01
            bg_c = (55, 44, 22) if is_sel else (self._MM_BTN_HOV if hov else self._MM_BTN_NRM)
            bdr  = (200, 160, 60) if (is_sel or hov) else self._MM_BORDER
            bdr_w = 2 if is_sel else 1
            pygame.draw.rect(self.screen, bg_c, rect, border_radius=4)
            pygame.draw.rect(self.screen, bdr,  rect, bdr_w, border_radius=4)
            lbl = self.font_md.render(label, False, (200, 160, 60) if is_sel else self._MM_BTN_TXT)
            self.screen.blit(lbl, lbl.get_rect(center=rect.center))
            if hov and clicked and not is_sel:
                return f"resolution:{val}"

        # Botão Voltar
        back = pygame.Rect(px + PW // 2 - self._u(80), py + PH - self._u(44), self._u(160), self._u(34))
        hov  = back.collidepoint(mx, my)
        self._mm_button(back, "Back", hov)
        if hov and clicked:
            self._pause_submenu = ""
        return None

    # ── Submenu Sound ──────────────────────────────────────────────────────
    # ── Submenu Interface (UI Scale) ───────────────────────────────────────
    def _draw_interface_submenu(self, events: list) -> "str | None":
        _STEPS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
        self._set_panel_scale(UI.MENU_INTERFACE_W, UI.MENU_INTERFACE_H)
        PW, PH  = self._u(UI.MENU_INTERFACE_W), self._u(UI.MENU_INTERFACE_H)
        self._mm_overlay()
        px, py  = self._mm_panel(PW, PH, (UI.MENU_INTERFACE_OFFSET_X, UI.MENU_INTERFACE_OFFSET_Y))

        title = self.font_md.render("Interface", False, self._MM_TITLE_COL)
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + self._u(14)))

        # Label
        lbl = self.font_sm.render("Escala da UI", False, (190, 175, 130))
        self.screen.blit(lbl, (px + self._u(24), py + self._u(62)))

        # Valor atual
        cur_s = self.font_sm.render(f"{self._ui_scale:.2f}×", False, (230, 210, 120))
        self.screen.blit(cur_s, (px + PW - cur_s.get_width() - self._u(24), py + self._u(62)))

        # Barra / botões −  +
        mx, my = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)

        btn_y = py + self._u(100)
        btn_w, btn_h = self._u(44), self._u(32)
        gap = self._u(12)

        minus_r = pygame.Rect(px + self._u(24), btn_y, btn_w, btn_h)
        plus_r  = pygame.Rect(px + PW - self._u(24) - btn_w, btn_y, btn_w, btn_h)

        for r, sym in [(minus_r, "−"), (plus_r, "+")]:
            hov = r.collidepoint(mx, my)
            pygame.draw.rect(self.screen, (55, 45, 25) if hov else (38, 30, 14), r, border_radius=5)
            pygame.draw.rect(self.screen, (140, 115, 60), r, 1, border_radius=5)
            ss = self.font_md.render(sym, False, (230, 210, 120))
            self.screen.blit(ss, ss.get_rect(center=r.center))

        # Pontinhos de passo
        pip_gap = self._u(18)
        total_pip_w = len(_STEPS) * pip_gap
        pip_x0 = px + PW // 2 - total_pip_w // 2
        for i, step in enumerate(_STEPS):
            active = abs(step - self._ui_scale) < 0.01
            col = (220, 190, 80) if active else (80, 65, 35)
            pygame.draw.circle(self.screen, col, (pip_x0 + i * pip_gap, btn_y + btn_h // 2), self._u(5))

        if clicked:
            cur_idx = min(range(len(_STEPS)), key=lambda i: abs(_STEPS[i] - self._ui_scale))
            if minus_r.collidepoint(mx, my) and cur_idx > 0:
                self._set_ui_scale(_STEPS[cur_idx - 1])
            elif plus_r.collidepoint(mx, my) and cur_idx < len(_STEPS) - 1:
                self._set_ui_scale(_STEPS[cur_idx + 1])

        # Botão Voltar
        back_r = pygame.Rect(px + PW // 2 - self._u(70), py + PH - self._u(48), self._u(140), self._u(34))
        hov_b  = back_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (55, 44, 24) if hov_b else (38, 30, 14), back_r, border_radius=6)
        pygame.draw.rect(self.screen, (110, 90, 50), back_r, 1, border_radius=6)
        bs = self.font_sm.render("← Voltar", False, (210, 192, 135))
        self.screen.blit(bs, bs.get_rect(center=back_r.center))
        if clicked and hov_b:
            self._pause_submenu = ""
        return None

    def _draw_sound_submenu(self, events: list) -> "str | None":
        self._set_panel_scale(UI.MENU_SOUND_W, UI.MENU_SOUND_H)
        PW, PH  = self._u(UI.MENU_SOUND_W), self._u(UI.MENU_SOUND_H)
        self._mm_overlay()
        px, py  = self._mm_panel(PW, PH, (UI.MENU_SOUND_OFFSET_X, UI.MENU_SOUND_OFFSET_Y))

        title = self.font_md.render("Sound", False, self._MM_TITLE_COL)
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + self._u(14)))

        mx, my   = pygame.mouse.get_pos()
        clicked  = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        released = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in events)
        if released:
            if self._sound_drag:
                self._save_config()
            self._sound_drag = ""

        # Layout: [ label | ████ slider ████ | toggle ]
        SLX     = px + self._u(100)          # slider start x
        SL_W    = self._u(190)               # slider track width
        SL_H    = self._u(10)                # slider track height
        TOG_X   = px + self._u(305)          # toggle start x
        TOG_W, TOG_H = self._u(64), self._u(26)

        rows = [
            ("Music",   "music",  SOUNDS.music_volume,  SOUNDS.music_enabled),
            ("Effects", "sfx",    SOUNDS.sfx_volume,    SOUNDS.sfx_enabled),
        ]

        for i, (label, key, vol, enabled) in enumerate(rows):
            row_y = py + self._u(70) + i * self._u(60)

            # Label
            lbl = self.font_sm.render(label, False, (200, 185, 155))
            self.screen.blit(lbl, (px + self._u(16), row_y + self._u(2)))

            # Slider track
            track = pygame.Rect(SLX, row_y, SL_W, SL_H)
            pygame.draw.rect(self.screen, (50, 42, 28), track, border_radius=5)
            fill_w = int(SL_W * vol)
            if fill_w > 0:
                fill_col = (200, 160, 60) if enabled else (100, 90, 60)
                pygame.draw.rect(self.screen, fill_col,
                                 pygame.Rect(SLX, row_y, fill_w, SL_H), border_radius=5)
            pygame.draw.rect(self.screen, (90, 72, 44), track, 1, border_radius=5)

            # Slider handle
            hx = SLX + int(SL_W * vol)
            pygame.draw.circle(self.screen, (220, 190, 110), (hx, row_y + SL_H // 2), self._u(7))

            # Click / drag on slider
            handle_area = pygame.Rect(SLX - self._u(8), row_y - self._u(8), SL_W + self._u(16), SL_H + self._u(16))
            if clicked and handle_area.collidepoint(mx, my):
                self._sound_drag = key
            if self._sound_drag == key:
                ratio = max(0.0, min(1.0, (mx - SLX) / SL_W))
                if key == "music":
                    SOUNDS.music_volume = ratio
                    SOUNDS.apply_music_settings()
                else:
                    SOUNDS.sfx_volume = ratio

            # Toggle
            tog_rect = pygame.Rect(TOG_X, row_y - self._u(8), TOG_W, TOG_H)
            tog_on_c = (50, 160, 80) if enabled else (60, 50, 40)
            tog_bd_c = (80, 200, 100) if enabled else self._MM_BORDER
            pygame.draw.rect(self.screen, tog_on_c,  tog_rect, border_radius=13)
            pygame.draw.rect(self.screen, tog_bd_c,  tog_rect, 1, border_radius=13)
            tog_txt = self.font_sm.render("ON" if enabled else "OFF", False,
                                          (200, 255, 200) if enabled else (160, 140, 120))
            self.screen.blit(tog_txt, tog_txt.get_rect(center=tog_rect.center))
            if clicked and tog_rect.collidepoint(mx, my):
                if key == "music":
                    SOUNDS.music_enabled = not SOUNDS.music_enabled
                    SOUNDS.apply_music_settings()
                else:
                    SOUNDS.sfx_enabled = not SOUNDS.sfx_enabled
                self._save_config()

            # Percent label
            pct = self.font_sm.render(f"{int(vol * 100)}%", False, (160, 150, 120))
            self.screen.blit(pct, (SLX + SL_W + self._u(4), row_y - self._u(1)))

        # Botão Voltar
        back = pygame.Rect(px + PW // 2 - self._u(80), py + PH - self._u(44), self._u(160), self._u(34))
        hov  = back.collidepoint(mx, my)
        self._mm_button(back, "Back", hov)
        if hov and clicked:
            self._pause_submenu = ""
            self._sound_drag    = ""
        return None
