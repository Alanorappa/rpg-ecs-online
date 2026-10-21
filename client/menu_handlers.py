"""
menu_handlers.py — Mixin com o menu de pausa (ESC) e seus submenus:
tela principal, confirmação de saída, resolução, interface (escala de
UI) e som. Separado de game.py para manter GameEngine conciso. Esta
classe NÃO deve ser instanciada diretamente — ela é herdada por
GameEngine, que fornece self.screen, self.font_md/font_sm, self._scale/
_ui_scale e os demais atributos referenciados aqui.

Todas as telas são montadas com tiles 16x16 do atlas de UI do usuário
(ui/ui_atlas.py — painel de madeira, botões, setas), no estilo do mockup
assets/content/exemplo.png, em escala FIXA (ui_atlas.SCALE, igual
nameplate) — nunca a escala de UI do jogador. Helpers _mm_* abaixo são o
ponto comum: painel, botão (age no SOLTAR do mouse), seletor ◀ valor ▶.
"""
import math

import pygame
from ui.ui_helpers import fill_surf

from ui.sound_manager import SOUNDS
from ui.ui_sizes import UI
from ui import ui_atlas


class MenuHandlers:
    _MM_TITLE_COL = (220, 190, 110)
    _MM_BTN_TXT   = (230, 210, 160)
    _MM_TEXT_COL  = (240, 225, 185)
    _MM_TITLE_Y   = ui_atlas.TILE   # centro (RAW) do título de cada janela

    # ── helpers comuns (atlas de UI) ──────────────────────────────────────

    def _mm_overlay(self):
        self.screen.blit(fill_surf((self.screen.get_width(), self.screen.get_height()), (0, 0, 0, 150)), (0, 0))

    def _mm_begin(self, events: list, tw: int, th: int, offset=(0, 0)) -> dict:
        """Escurece o fundo, cria o painel de madeira (tw x th tiles, RAW,
        cópia pronta pra carimbar por cima) centralizado, e lê o mouse já
        convertido pra espaço RAW do painel."""
        S = ui_atlas.SCALE
        raw = ui_atlas.wood_panel_raw(tw, th).copy()
        pw, ph = raw.get_width() * S, raw.get_height() * S
        px = self.screen.get_width()  // 2 - pw // 2 + offset[0]
        py = self.screen.get_height() // 2 - ph // 2 + offset[1]
        self._mm_overlay()
        mx, my = pygame.mouse.get_pos()
        return {
            "raw": raw, "px": px, "py": py, "pw": pw, "ph": ph,
            "rx": (mx - px) / S, "ry": (my - py) / S,
            "clicked":  any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events),
            "released": any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in events),
            "down":     pygame.mouse.get_pressed()[0],
            "texts":    [],   # (texto, cx_raw, cy_raw, cor) — desenhados por cima no fim
        }

    def _mm_press(self, f: dict, btn_id: str, x0, y0, x1, y1) -> tuple:
        """Estado de um botão na área RAW (x0,y0)-(x1,y1). Age no SOLTAR
        (agir no clique fecha/troca a janela no mesmo frame e o estado
        pressionado nunca aparece); soltar fora cancela. Devolve
        (desenhar_pressionado, disparou)."""
        hov = x0 <= f["rx"] < x1 and y0 <= f["ry"] < y1
        if f["clicked"] and hov:
            self._mm_pressed = btn_id
        held  = self._mm_pressed == btn_id
        fired = f["released"] and hov and held
        return held and hov and not f["released"], fired

    def _mm_button(self, f: dict, btn_id: str, tx: int, ty: int, w: int,
                   label: str, tall: bool = True) -> bool:
        """Botão do atlas (w tiles de largura) com texto. True = disparou."""
        T = ui_atlas.TILE
        ay0, ay1 = ui_atlas.button_art_y(tall)
        x0, x1 = tx * T, (tx + w) * T
        y0, y1 = ty * T + ay0, ty * T + ay1
        pressed, fired = self._mm_press(f, btn_id, x0, y0, x1, y1)
        ui_atlas.blit_button(f["raw"], tx, ty, w, pressed=pressed, tall=tall)
        f["texts"].append((label, (x0 + x1) / 2, (y0 + y1) / 2, self._MM_BTN_TXT))
        return fired

    def _mm_selector(self, f: dict, sel_id: str, ty: int, col_l: int, col_r: int,
                     text: str) -> int:
        """Linha ◀ texto ▶ (setas do atlas nas colunas col_l/col_r, texto
        centralizado entre elas). Devolve -1/+1 quando uma seta dispara
        (no soltar), 0 caso contrário."""
        T = ui_atlas.TILE
        step = 0
        for col, d, idle, pressed_t in ((col_l, -1, ui_atlas.ARROW_LEFT, ui_atlas.ARROW_LEFT_PRESSED),
                                        (col_r, +1, ui_atlas.ARROW_RIGHT, ui_atlas.ARROW_RIGHT_PRESSED)):
            pressed, fired = self._mm_press(f, f"{sel_id}:{d}", col * T, ty * T, (col + 1) * T, (ty + 1) * T)
            ui_atlas.blit_tile(f["raw"], pressed_t if pressed else idle, col, ty)
            if fired:
                step = d
        f["texts"].append((text, (col_l + 1 + col_r) * T / 2, ty * T + T / 2, self._MM_TEXT_COL))
        return step

    def _mm_text(self, f: dict, text: str, cx_raw: float, cy_raw: float, color=None) -> None:
        f["texts"].append((text, cx_raw, cy_raw, color or self._MM_TEXT_COL))

    def _mm_end(self, f: dict, title: str) -> None:
        """Amplia o painel pro tamanho de tela, desenha título e textos por
        cima e encerra o estado de botão pressionado ao soltar o mouse."""
        S = ui_atlas.SCALE
        px, py = f["px"], f["py"]
        self.screen.blit(ui_atlas.scale_up(f["raw"]), (px, py))
        surf = self.font_md.render(title, False, self._MM_TITLE_COL)
        self.screen.blit(surf, surf.get_rect(center=(px + f["pw"] // 2, py + self._MM_TITLE_Y * S)))
        for text, cx, cy, color in f["texts"]:
            surf = self.font_md.render(text, False, color)
            self.screen.blit(surf, surf.get_rect(center=(px + cx * S, py + cy * S)))
        if f["released"] or not f["down"]:
            self._mm_pressed = ""

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

    # ── Larguras a partir do TEXTO ─────────────────────────────────────────
    # A fonte acompanha a escala de UI do jogador, mas o atlas é fixo em
    # SCALE — larguras de botão/seletor/janela NUNCA podem ser número
    # fixo de tiles (bug real: texto vazando do botão com escala de UI
    # 1.25). Mede com a MESMA fonte usada pra desenhar (font_md).
    _MM_TEXT_PAD = 6   # px RAW de folga de cada lado (bordas do botão)

    def _mm_tiles_for(self, texts, min_tiles: int) -> int:
        widest = max(self.font_md.size(t)[0] for t in texts) / ui_atlas.SCALE
        return max(min_tiles, math.ceil((widest + 2 * self._MM_TEXT_PAD) / ui_atlas.TILE))

    def _mm_selector_layout(self, values, min_inner: int = 5) -> tuple:
        """(tw, inner) — janela com seletor ◀ texto ▶: borda, seta, `inner`
        tiles de texto, seta, borda. `values` = TODOS os textos possíveis
        do seletor (a largura não muda ao trocar de valor)."""
        inner = self._mm_tiles_for(values, min_inner)
        return inner + 4, inner

    # Mesma montagem do mockup: botões "baixos" empilhados (1 por linha a
    # partir da linha 2), título na linha 1, largura cresce com o texto.
    _MAIN_BTNS = [
        ("Continuar",           "resume"),
        ("Resolução",           "submenu:resolution"),
        ("Som",                 "submenu:sound"),
        ("Interface",           "submenu:interface"),
        ("Atalhos do teclado",  "open_hotbar_editor"),
        ("Voltar ao Spawn",     "unstuck"),
        ("Deslogar",            "logout"),
        ("Sair",                "submenu:quit_confirm"),
    ]
    _MAIN_ROW0 = 2

    def _main_layout(self) -> tuple:
        """(tw, th, btn_w) — botão de 1 tile de borda a cada lado."""
        btn_w = self._mm_tiles_for([l for l, _ in self._MAIN_BTNS], 5)
        return btn_w + 2, self._MAIN_ROW0 + len(self._MAIN_BTNS) + 1, btn_w

    def _draw_main_menu(self, events: list) -> "str | None":
        tw, th, btn_w = self._main_layout()
        f = self._mm_begin(events, tw, th, (UI.MENU_MAIN_OFFSET_X, UI.MENU_MAIN_OFFSET_Y))
        action = None
        for i, (label, act) in enumerate(self._MAIN_BTNS):
            if self._mm_button(f, f"main:{act}", 1, self._MAIN_ROW0 + i, btn_w, label, tall=False):
                action = act
        self._mm_end(f, "Menu")
        if action and action.startswith("submenu:"):
            self._pause_submenu = action.split(":")[1]
            return None
        return action

    # ── Confirmação de saída ───────────────────────────────────────────────
    _QUIT_MSG = "Tem certeza que deseja sair?"
    _QUIT_TH, _QUIT_BTN_ROW = 5, 3

    def _quit_layout(self) -> tuple:
        """(tw, btn_w, col_sim, col_nao) — 2 botões com 1 tile de vão."""
        btn_w = self._mm_tiles_for(["Sim", "Não"], 3)
        tw = max(self._mm_tiles_for([self._QUIT_MSG], 1) + 2, 2 * btn_w + 3)
        col_sim = (tw - (2 * btn_w + 1)) // 2
        return tw, btn_w, col_sim, col_sim + btn_w + 1

    def _draw_quit_confirm(self, events: list) -> "str | None":
        tw, btn_w, col_sim, col_nao = self._quit_layout()
        f = self._mm_begin(events, tw, self._QUIT_TH,
                           (UI.MENU_QUIT_CONFIRM_OFFSET_X, UI.MENU_QUIT_CONFIRM_OFFSET_Y))
        T = ui_atlas.TILE
        self._mm_text(f, self._QUIT_MSG, tw * T / 2, 2 * T)
        sim = self._mm_button(f, "quit:sim", col_sim, self._QUIT_BTN_ROW, btn_w, "Sim", tall=False)
        nao = self._mm_button(f, "quit:nao", col_nao, self._QUIT_BTN_ROW, btn_w, "Não", tall=False)
        self._mm_end(f, "Sair")
        if sim:
            return "quit"
        if nao:
            self._pause_submenu = ""
        return None

    # ── Submenu Resolução (24/07/2026 — escala + toggle Tela cheia/Janela) ──
    # Escala (1x/1.25x/1.5x) e modo de janela viram 2 seletores ◀ valor ▶
    # (decisão do usuário, 20/10/2026). "windowed" (fallback de erro
    # legado, _compute_and_set_window_mode()) conta como "Janela" — nunca
    # alcançável por aqui, só tolerado.
    _RES_TH = 10
    _RES_ROW_SCALE, _RES_ROW_MODE = 3, 5
    _RES_BTN_ROW = 7

    def _res_layout(self) -> tuple:
        from ui.settings_screen import SCALE_OPTIONS
        return self._mm_selector_layout([l for l, _ in SCALE_OPTIONS] + ["Tela cheia", "Janela"])

    def _draw_resolution_submenu(self, events: list) -> "str | None":
        from ui.settings_screen import SCALE_OPTIONS
        tw, inner = self._res_layout()
        f = self._mm_begin(events, tw, self._RES_TH,
                           (UI.MENU_RESOLUTION_OFFSET_X, UI.MENU_RESOLUTION_OFFSET_Y))
        action = None
        col_r = inner + 2

        idx = min(range(len(SCALE_OPTIONS)), key=lambda i: abs(SCALE_OPTIONS[i][1] - self._scale))
        step = self._mm_selector(f, "res:scale", self._RES_ROW_SCALE, 1, col_r, SCALE_OPTIONS[idx][0])
        new_idx = idx + step
        if step and 0 <= new_idx < len(SCALE_OPTIONS):
            action = f"resolution:{SCALE_OPTIONS[new_idx][1]}"

        is_fullscreen = (self._window_mode_pref == "fullscreen")
        if self._mm_selector(f, "res:mode", self._RES_ROW_MODE, 1, col_r,
                             "Tela cheia" if is_fullscreen else "Janela"):
            action = f"window_mode:{'windowed_fullsize' if is_fullscreen else 'fullscreen'}"

        back = self._mm_back_button(f, "res:voltar", tw, self._RES_BTN_ROW)
        self._mm_end(f, "Resolução")
        if back:
            self._pause_submenu = ""
        return action

    def _mm_back_button(self, f: dict, btn_id: str, tw: int, ty: int) -> bool:
        """Botão "Voltar" (alto) centralizado na janela de tw tiles."""
        w = self._mm_tiles_for(["Voltar"], 3)
        return self._mm_button(f, btn_id, (tw - w) // 2, ty, w, "Voltar")

    # ── Submenu Interface (escala da UI) ───────────────────────────────────
    _IFACE_STEPS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
    _IFACE_LABEL = "Escala da UI"
    _IFACE_TH = 9
    _IFACE_ROW_LABEL, _IFACE_ROW_SEL = 3, 4
    _IFACE_BTN_ROW = 6

    def _iface_layout(self) -> tuple:
        tw, inner = self._mm_selector_layout([f"{s:.2f}×" for s in self._IFACE_STEPS])
        return max(tw, self._mm_tiles_for([self._IFACE_LABEL], 1) + 2), inner

    def _draw_interface_submenu(self, events: list) -> "str | None":
        tw, inner = self._iface_layout()
        f = self._mm_begin(events, tw, self._IFACE_TH,
                           (UI.MENU_INTERFACE_OFFSET_X, UI.MENU_INTERFACE_OFFSET_Y))
        T = ui_atlas.TILE
        steps = self._IFACE_STEPS
        cur = min(range(len(steps)), key=lambda i: abs(steps[i] - self._ui_scale))
        self._mm_text(f, self._IFACE_LABEL, tw * T / 2,
                      self._IFACE_ROW_LABEL * T + T / 2, self._MM_TITLE_COL)
        col_l = (tw - (inner + 2)) // 2
        step = self._mm_selector(f, "iface:scale", self._IFACE_ROW_SEL, col_l, col_l + inner + 1,
                                 f"{steps[cur]:.2f}×")
        back = self._mm_back_button(f, "iface:voltar", tw, self._IFACE_BTN_ROW)
        self._mm_end(f, "Interface")
        if step and 0 <= cur + step < len(steps):
            self._set_ui_scale(steps[cur + step])
        if back:
            self._pause_submenu = ""
        return None

    # ── Submenu Som ────────────────────────────────────────────────────────
    def _draw_sound_submenu(self, events: list) -> "str | None":
        """Em cada linha de volume: ícone (clicar = mudo/desmudo; 0% também
        mostra desligado), seta esq./dir. (clique = ±1%; segurar = ±1
        barrinha a cada _SND_HOLD_MS), trilho com barrinhas proporcionais
        ao volume (arrastar sobre o trilho ajusta direto) e o percentual
        acima."""
        f = self._mm_begin(events, self._SND_TW, self._SND_TH,
                           (UI.MENU_SOUND_OFFSET_X, UI.MENU_SOUND_OFFSET_Y))
        T, raw = ui_atlas.TILE, f["raw"]
        rx, ry, clicked = f["rx"], f["ry"], f["clicked"]
        now = pygame.time.get_ticks()
        if f["released"] or not f["down"]:
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
            audible = enabled and round(vol * 100) > 0
            ui_atlas.blit_tile(raw, icon_on if audible else icon_off, self._SND_COL_ICON, ty)
            ui_atlas.blit_tile(raw, ui_atlas.ARROW_LEFT_PRESSED if l_pressed else ui_atlas.ARROW_LEFT,
                               self._SND_COL_ARROW_L, ty)
            for col in range(self._SND_COL_ARROW_L + 1, self._SND_COL_ARROW_R):
                ui_atlas.blit_tile(raw, ui_atlas.SLIDER_TRACK, col, ty)
            ui_atlas.blit_tile(raw, ui_atlas.ARROW_RIGHT_PRESSED if r_pressed else ui_atlas.ARROW_RIGHT,
                               self._SND_COL_ARROW_R, ty)
            for k in range(round(vol * n_max)):
                ui_atlas.blit_tile_px(raw, ui_atlas.SLIDER_FILL_BAR, bar_x0 + k * bar_w, ty * T)
            self._mm_text(f, f"{round(vol * 100)}%", bar_x0 + n_max * bar_w / 2,
                          ty * T - self._SND_PCT_GAP, self._MM_BTN_TXT)

        back = self._mm_back_button(f, "snd:voltar", self._SND_TW, self._SND_BTN_ROW)
        self._mm_end(f, "Som")
        if back:
            self._pause_submenu    = ""
            self._sound_drag       = ""
            self._sound_arrow_hold = None
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

    # Layout da janela de som em TILES (estilo do mockup
    # assets/content/exemplo.png). Trilho/barrinhas ocupam as colunas
    # entre as duas setas.
    _SND_TW, _SND_TH     = 9, 11
    _SND_ROW_MUSIC       = 3
    _SND_ROW_SFX         = 6
    _SND_COL_ICON        = 1
    _SND_COL_ARROW_L     = 2
    _SND_COL_ARROW_R     = 7
    _SND_BTN_ROW         = 8
    _SND_PCT_GAP         = 6     # px RAW entre o centro do percentual e o topo da linha do slider
    _SND_HOLD_MS         = 250   # segurar a seta: 1 barrinha a cada N ms
