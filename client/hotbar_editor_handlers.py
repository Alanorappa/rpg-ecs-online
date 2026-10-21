"""
hotbar_editor_handlers.py — Mixin com o modal "Atalhos do teclado"
(tecla K / menu de pausa): rebind de teclas de menus, slots de
habilidade e consumíveis, com captura de tecla e botões Salvar/Fechar.
Separado de game.py para manter GameEngine conciso. Esta classe NÃO
deve ser instanciada diretamente — ela é herdada por GameEngine, que
fornece self.world, self.player_entity, self.screen, self.font_*,
self._menu_keys, self._mkb_rebind e os helpers _mm_* (ui/atlas_window.py via
(client/menu_handlers.py — mesmo GameEngine).

Visual (20/10/2026): atlas de UI (ui/ui_atlas.py) no estilo da janela de
seleção de personagem do mockup — painel escuro, 1 moldura por seção
(decisão do usuário), botão da tecla em cada linha, barra de rolagem na
borda direita com setas clicáveis (segurar repete) e puxador arrastável.
"""
import pygame

from engine.components import PlayerSkills
from ui import atlas_window as aw
from ui import ui_atlas
from ui.ui_sizes import UI


class HotbarEditorHandlers:
    _HBE_HOLD_MS = 250   # segurar seta da rolagem: 1 linha a cada N ms

    _HBE_MENU_ROWS = [("Inventário", "inventario"), ("Talentos", "talentos"),
                      ("Mapa", "mapa"), ("Diário de Quests", "diario"),
                      ("Habilidades", "habilidades"), ("Estatísticas", "estatisticas")]
    _HBE_HINT_IDLE = "Clique na tecla para trocar  |  ESC para fechar"
    _HBE_HINT_WAIT = "Pressione a nova tecla  |  ESC para cancelar"

    def _close_hotbar_editor(self) -> None:
        self._show_hotbar_editor    = False
        self._hbe_drag_from         = None
        self._hbe_rebind_slot       = None
        self._hbe_cons_drag_from    = None
        self._hbe_cons_rebind_slot  = None
        self._hbe_tab               = 0
        self._mkb_rebind            = None
        self._hbe_scroll            = aw.ScrollState()
        self._hbe_skill_scroll      = 0
        self._hbe_expand_slots      = False
        self._save_config()

    @staticmethod
    def _hbe_key_name(code: int) -> str:
        return pygame.key.name(code).upper() if code else "—"

    def _hbe_sections(self, ps, cbar) -> list:
        """[(título, [(rótulo, keycode, key_id), ...]), ...]"""
        from content.skill_config import NUM_SLOTS
        from engine.components import ConsumableBar as _CB
        menus = [(l, self._menu_keys.get(mid, 0), f"menu:{mid}") for l, mid in self._HBE_MENU_ROWS]
        skills = [(f"Slot {i + 1}", ps.keybinds[i] if i < len(ps.keybinds) else 0, f"slot:{i}")
                  for i in range(NUM_SLOTS)]
        cons = [(f"Slot {NUM_SLOTS + i + 1}",
                 cbar.keybinds[i] if cbar and i < len(cbar.keybinds) else 0, f"cons:{i}")
                for i in range(_CB.NUM_SLOTS)]
        return [("Menus", menus), ("Barra de Habilidades", skills), ("Barra de Consumíveis", cons)]

    def _hbe_layout(self, sections) -> dict:
        """Tamanhos em tiles, a partir do TEXTO (fonte acompanha a escala
        de UI do jogador, o atlas não). Colunas: moldura (col 0), rótulo
        (L tiles), vão, tecla (K tiles), moldura, barra de rolagem
        (coluna da borda direita, como no mockup)."""
        T, S = ui_atlas.TILE, ui_atlas.SCALE
        labels = [t for t, _ in sections] + [r[0] for _, rows in sections for r in rows]
        keys = [self._hbe_key_name(r[1]) for _, rows in sections for r in rows] + ["..."]
        L = self._mm_tiles_for(labels, 4)
        K = self._mm_tiles_for(keys, 3)
        hint_tiles = self._mm_tiles_for([self._HBE_HINT_IDLE, self._HBE_HINT_WAIT], 1, self.font_sm)
        title_tiles = self._mm_tiles_for(["Atalhos do teclado"], 1)
        tw = max(L + K + 4, hint_tiles + 2, title_tiles + 2)
        L = tw - K - 4
        content_rows = sum(len(rows) + 3 for _, rows in sections)
        # Altura: limitada à área segura padrão (`_mm_safe_area` — margem
        # no topo e acima da barra de ações). Lista que não cabe rola.
        max_th = self._mm_safe_area().height // (T * S)
        view_rows = max(3, min(content_rows, max_th - 5))
        btn_w = aw.button_tiles(self.font_md, ["Salvar", "Fechar"], 3)
        return {"tw": tw, "th": view_rows + 5, "L": L, "K": K, "view_rows": view_rows,
                "content_rows": content_rows, "view_y0": 2 * T, "btn_w": btn_w}

    def _draw_hotbar_editor(self, events: list) -> None:
        """Modal 'Atalhos do teclado' — seções com rebind + Salvar / Fechar."""
        from engine.components import ConsumableBar as _CB

        ps   = self.world.get_component(self.player_entity, PlayerSkills)
        cbar = self.world.get_component(self.player_entity, _CB)
        if not ps:
            return

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

        T, S = ui_atlas.TILE, ui_atlas.SCALE
        sections = self._hbe_sections(ps, cbar)
        lay = self._hbe_layout(sections)
        tw, th, L, K = lay["tw"], lay["th"], lay["L"], lay["K"]
        vy0, vh = lay["view_y0"], lay["view_rows"] * T
        content_h = lay["content_rows"] * T
        max_scroll = max(0, content_h - vh)

        f = self._mm_begin(events, tw, th, (UI.HOTBAR_EDITOR_OFFSET_X, UI.HOTBAR_EDITOR_OFFSET_Y),
                           style="dark")
        rx, ry = f.rx, f.ry
        # ── Barra de rolagem (coluna da borda direita) ─────────────────────
        sb_col = tw - 1
        f.scrollbar(self._hbe_scroll, sb_col, 2, 2 + lay["view_rows"] - 1, max_scroll,
                    hold_ms=self._HBE_HOLD_MS)

        # ── Conteúdo rolável (montado inteiro, recortado pela janela) ──────
        scroll = self._hbe_scroll.offset
        in_view = vy0 <= ry < vy0 + vh and 0 <= rx < sb_col * T
        fc = f.child(pygame.Surface((sb_col * T, content_h), pygame.SRCALPHA),
                     ry - vy0 + scroll if in_view else -10_000)
        key_col = L + 2
        r = 0
        for title, rows in sections:
            fc.text(title, T, r * T + T / 2, aw.TITLE_COL, anchor="midleft")
            r += 1
            ui_atlas.blit_frame(fc.raw, 0, r, L + K + 3, len(rows) + 2)
            for i, (label, code, key_id) in enumerate(rows):
                rr = r + 1 + i
                fc.text(label, T + 4, rr * T + T / 2, anchor="midleft")
                waiting = self._mkb_rebind == key_id
                if fc.text_field(f"hbe:{key_id}", key_col, rr, K,
                                       "..." if waiting else self._hbe_key_name(code),
                                       active=waiting):
                    self._mkb_rebind = key_id
            r += len(rows) + 2

        # ── Dica + Salvar / Fechar ────────────────────────────────────────
        f.text(self._HBE_HINT_WAIT if self._mkb_rebind else self._HBE_HINT_IDLE,
                      tw * T / 2, (th - 3) * T + T / 2, (200, 180, 80) if self._mkb_rebind else None,
                      font=self.font_sm)
        bw = lay["btn_w"]
        col0 = (tw - (2 * bw + 1)) // 2
        save = f.button("hbe:salvar", col0, th - 2, bw, "Salvar", tall=False)
        close = f.button("hbe:fechar", col0 + bw + 1, th - 2, bw, "Fechar", tall=False)

        f.end("Atalhos do teclado")

        content = ui_atlas.scale_up(fc.raw)
        aw.draw_texts(content, fc.texts, self.font_md)
        view = content.subsurface((0, scroll * S, content.get_width(), vh * S))
        self.screen.blit(view, (f.px, f.py + vy0 * S))

        if save or close:
            self._save_config()
            if close:
                self._close_hotbar_editor()
