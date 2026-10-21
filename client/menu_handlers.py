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
import math

import pygame
from ui.ui_helpers import fill_surf

from ui.sound_manager import SOUNDS
from ui.ui_sizes import UI
from ui import ui_atlas


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
            ("Deslogar",            "logout"),
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

    # ── Submenu Resolution (24/07/2026 — vira um toggle Tela cheia/Janela) ──
    # Antes tinha um picker de escala (1x/1.25x/1.5x, janela de tamanho
    # FIXO) + um submenu "Janela" separado com 3 modos. Removido a pedido
    # do usuário: numa resolução que bate com o monitor, "janela de
    # tamanho fixo" e "tela cheia" ficavam visualmente idênticas (sem
    # nenhuma borda visível) — redundante e confuso. Só sobram 2 modos
    # de janela ("Tela cheia" e "Janela" = windowed_fullsize), os dois
    # com resolução lógica = tamanho real da janela/monitor (zero
    # distorção do SCALED, ver §34.48/§34.49). Pedido do usuário: manter
    # o picker de escala (Resolution) que já existia, E colocar o toggle
    # de modo de janela no MESMO submenu (sem botão novo no menu
    # principal) — E que seja um toggle DE VERDADE (1 controle,
    # track+knob, 1 clique alterna), não 2 botões lado a lado. "windowed"
    # (tamanho fixo, sem auto-detecção) continua existindo no código como
    # fallback de erro — _compute_and_set_window_mode() — só não é mais
    # alcançável por aqui.
    def _draw_resolution_submenu(self, events: list) -> "str | None":
        from ui.settings_screen import SCALE_OPTIONS
        self._set_panel_scale(UI.MENU_RESOLUTION_W, UI.MENU_RESOLUTION_H)
        PW, PH = self._u(UI.MENU_RESOLUTION_W), self._u(UI.MENU_RESOLUTION_H)
        self._mm_overlay()
        px, py = self._mm_panel(PW, PH, (UI.MENU_RESOLUTION_OFFSET_X, UI.MENU_RESOLUTION_OFFSET_Y))

        title = self.font_md.render("Resolution", False, self._MM_TITLE_COL)
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + self._u(14)))

        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)

        # Picker de escala (1x/1.25x/1.5x) — igual sempre foi.
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

        # "Modo:      Tela cheia/Janela" — toggle de verdade (1 track +
        # 1 knob que desliza, 1 clique alterna pro modo oposto).
        mode_y = py + self._u(52) + len(SCALE_OPTIONS) * (opt_h + self._u(8)) + self._u(8)
        lbl = self.font_sm.render("Modo:      Tela cheia/Janela", False, (190, 175, 130))
        self.screen.blit(lbl, (px + self._u(24), mode_y + self._u(4)))

        track_w, track_h = self._u(46), self._u(22)
        track_rect = pygame.Rect(px + PW - self._u(24) - track_w, mode_y, track_w, track_h)
        # "windowed" (fallback de erro legado) conta como "Janela" —
        # nunca fica sem posição nenhuma pro knob.
        is_fullscreen = (self._window_mode_pref == "fullscreen")
        toggle_area = track_rect.inflate(self._u(10), self._u(10))
        hov_toggle = toggle_area.collidepoint(mx, my)
        track_bg = self._MM_BTN_HOV if hov_toggle else (55, 44, 22)
        pygame.draw.rect(self.screen, track_bg, track_rect, border_radius=track_h // 2)
        pygame.draw.rect(self.screen, (200, 160, 60) if hov_toggle else self._MM_BORDER,
                         track_rect, 1, border_radius=track_h // 2)
        knob_r  = (track_h - self._u(4)) // 2
        knob_cx = (track_rect.left + knob_r + self._u(2) if is_fullscreen
                   else track_rect.right - knob_r - self._u(2))
        pygame.draw.circle(self.screen, (220, 190, 110), (knob_cx, track_rect.centery), knob_r)
        if hov_toggle and clicked:
            new_mode = "windowed_fullsize" if is_fullscreen else "fullscreen"
            return f"window_mode:{new_mode}"

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
        """Montada com tiles 16x16 do atlas de UI (ui/ui_atlas.py), no
        estilo do mockup do usuário (assets/content/exemplo.png). Em cada
        linha de volume: ícone (clicar = mudo/desmudo), seta esq./dir.
        (clique = ±1%; segurar = ±1 barrinha a cada _SND_HOLD_MS), trilho
        com barrinhas proporcionais ao volume (arrastar sobre o trilho
        ajusta direto) e o percentual acima. Escala fixa ui_atlas.SCALE
        (igual nameplate), nunca a escala de UI do jogador."""
        T, S = ui_atlas.TILE, ui_atlas.SCALE
        raw = ui_atlas.wood_panel_raw(self._SND_TW, self._SND_TH).copy()
        PW, PH = raw.get_width() * S, raw.get_height() * S
        px = self.screen.get_width()  // 2 - PW // 2 + UI.MENU_SOUND_OFFSET_X
        py = self.screen.get_height() // 2 - PH // 2 + UI.MENU_SOUND_OFFSET_Y
        self._mm_overlay()

        mx, my     = pygame.mouse.get_pos()
        rx, ry     = (mx - px) / S, (my - py) / S   # mouse em espaço RAW
        clicked    = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        released   = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in events)
        mouse_down = pygame.mouse.get_pressed()[0]
        now        = pygame.time.get_ticks()
        if released or not mouse_down:
            if self._sound_drag or self._sound_arrow_hold:
                self._save_config()
            self._sound_drag       = ""
            self._sound_arrow_hold = None

        bar_x0 = (self._SND_COL_ARROW_L + 1) * T
        bar_w  = ui_atlas.SLIDER_FILL_BAR_W
        n_max  = (self._SND_COL_ARROW_R - self._SND_COL_ARROW_L - 1) * T // bar_w

        rows = [
            ("music", self._SND_ROW_MUSIC, ui_atlas.ICON_MUSIC_ON, ui_atlas.ICON_MUSIC_OFF),
            ("sfx",   self._SND_ROW_SFX,   ui_atlas.ICON_SFX_ON,   ui_atlas.ICON_SFX_OFF),
        ]
        pct_labels = []
        for key, ty, icon_on, icon_off in rows:
            enabled = SOUNDS.music_enabled if key == "music" else SOUNDS.sfx_enabled
            in_row  = ty * T <= ry < (ty + 1) * T

            def over_col(col):
                return in_row and col * T <= rx < (col + 1) * T

            if clicked and over_col(self._SND_COL_ICON):
                if key == "music":
                    SOUNDS.music_enabled = not SOUNDS.music_enabled
                    SOUNDS.apply_music_settings()
                else:
                    SOUNDS.sfx_enabled = not SOUNDS.sfx_enabled
                enabled = not enabled
                self._save_config()

            # Setas: clique = ±1%; segurar = ±1 barrinha a cada _SND_HOLD_MS.
            for col, step in ((self._SND_COL_ARROW_L, -1), (self._SND_COL_ARROW_R, +1)):
                if clicked and over_col(col):
                    self._snd_set_volume(key, round(self._snd_volume(key) * 100 + step) / 100)
                    self._sound_arrow_hold = (key, step, now + self._SND_HOLD_MS)
            hold = self._sound_arrow_hold
            if hold and hold[0] == key and now >= hold[2]:
                held_col = self._SND_COL_ARROW_L if hold[1] < 0 else self._SND_COL_ARROW_R
                if over_col(held_col):
                    cur = self._snd_volume(key) * n_max
                    bars = (int(cur + 1e-6) + 1) if hold[1] > 0 else (math.ceil(cur - 1e-6) - 1)
                    self._snd_set_volume(key, bars / n_max)
                self._sound_arrow_hold = (key, hold[1], now + self._SND_HOLD_MS)

            # Arrastar: só sobre o trilho (nunca sobre as setas, que são botões).
            if clicked and in_row and bar_x0 <= rx < bar_x0 + n_max * bar_w:
                self._sound_drag = key
            if self._sound_drag == key:
                self._snd_set_volume(key, (rx - bar_x0) / (n_max * bar_w))

            hold = self._sound_arrow_hold
            l_pressed = bool(hold and hold[0] == key and hold[1] < 0)
            r_pressed = bool(hold and hold[0] == key and hold[1] > 0)
            vol = self._snd_volume(key)
            # 0% também é "sem som" — ícone de desligado, mesmo com o mute
            # (enabled) ainda ligado.
            audible = enabled and round(vol * 100) > 0
            ui_atlas.blit_tile(raw, icon_on if audible else icon_off, self._SND_COL_ICON, ty)
            ui_atlas.blit_tile(raw, ui_atlas.ARROW_LEFT_PRESSED if l_pressed else ui_atlas.ARROW_LEFT,
                               self._SND_COL_ARROW_L, ty)
            for col in range(self._SND_COL_ARROW_L + 1, self._SND_COL_ARROW_R):
                ui_atlas.blit_tile(raw, ui_atlas.SLIDER_TRACK, col, ty)
            ui_atlas.blit_tile(raw, ui_atlas.ARROW_RIGHT_PRESSED if r_pressed else ui_atlas.ARROW_RIGHT,
                               self._SND_COL_ARROW_R, ty)

            # Medidor segmentado: barrinhas coladas por cima do trilho,
            # quantidade proporcional ao volume.
            for k in range(round(vol * n_max)):
                ui_atlas.blit_tile_px(raw, ui_atlas.SLIDER_FILL_BAR, bar_x0 + k * bar_w, ty * T)
            pct_labels.append((f"{round(vol * 100)}%",
                               bar_x0 + n_max * bar_w / 2, ty * T - self._SND_PCT_GAP))

        # Botão Voltar — 3x2 tiles; a arte ocupa só a faixa BTN_ART_Y0..Y1
        # dentro do bloco, então o hit-test usa essa faixa (não os 32px).
        bx, by, bw = self._SND_BTN_COL, self._SND_BTN_ROW, self._SND_BTN_W
        b_x0, b_x1 = bx * T, (bx + bw) * T
        b_y0, b_y1 = by * T + ui_atlas.BTN_ART_Y0, by * T + ui_atlas.BTN_ART_Y1
        # Botão age no SOLTAR (não no clicar) — senão a janela fecha no
        # mesmo frame do clique e o estado "pressionado" nunca aparece.
        # Soltar fora do botão cancela.
        hov = b_x0 <= rx < b_x1 and b_y0 <= ry < b_y1
        if clicked and hov:
            self._sound_back_pressed = True
        go_back = released and hov and self._sound_back_pressed
        if released or not mouse_down:
            self._sound_back_pressed = False
        ui_atlas.blit_button(raw, bx, by, bw, pressed=self._sound_back_pressed and hov)

        self.screen.blit(ui_atlas.scale_up(raw), (px, py))

        title = self.font_md.render("Sound", False, self._MM_TITLE_COL)
        self.screen.blit(title, title.get_rect(center=(px + PW // 2, py + T * S)))
        for text, cx, cy in pct_labels:
            surf = self.font_md.render(text, False, self._MM_BTN_TXT)
            self.screen.blit(surf, surf.get_rect(center=(px + cx * S, py + cy * S)))
        lbl = self.font_md.render("Voltar", False, self._MM_BTN_TXT)
        self.screen.blit(lbl, lbl.get_rect(center=(px + (b_x0 + b_x1) * S // 2,
                                                   py + (b_y0 + b_y1) * S // 2)))
        if go_back:
            self._pause_submenu      = ""
            self._sound_drag         = ""
            self._sound_arrow_hold   = None
            self._sound_back_pressed = False
        return None

    @staticmethod
    def _snd_volume(key: str) -> float:
        return SOUNDS.music_volume if key == "music" else SOUNDS.sfx_volume

    @staticmethod
    def _snd_set_volume(key: str, value: float) -> None:
        value = max(0.0, min(1.0, value))
        if key == "music":
            SOUNDS.music_volume = value
            SOUNDS.apply_music_settings()
        else:
            SOUNDS.sfx_volume = value

    # Layout da janela de som em TILES (mesmas posições do mockup
    # assets/content/exemplo.png). Trilho/barrinhas ocupam as colunas
    # entre as duas setas.
    _SND_TW, _SND_TH     = 9, 11
    _SND_ROW_MUSIC       = 3
    _SND_ROW_SFX         = 6
    _SND_COL_ICON        = 1
    _SND_COL_ARROW_L     = 2
    _SND_COL_ARROW_R     = 7
    _SND_BTN_COL, _SND_BTN_ROW, _SND_BTN_W = 3, 8, 3
    _SND_PCT_GAP         = 6     # px RAW entre o centro do percentual e o topo da linha do slider
    _SND_HOLD_MS         = 250   # segurar a seta: 1 barrinha a cada N ms
