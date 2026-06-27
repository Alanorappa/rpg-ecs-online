"""
debug_handlers.py — Mixin com o modal de debug (F12): subir de
nível, adicionar ouro/itens e trocar de mapa, incluindo o roteamento
de cliques e o desenho do modal e de cada aba (Nivel/Itens/Ouro/Mapa).
Separado de game.py para manter GameEngine conciso. Esta classe NÃO
deve ser instanciada diretamente — ela é herdada por GameEngine, que
fornece self.world, self._my_eid, self.screen e os demais atributos
referenciados aqui.
"""
import pygame

from combat_log import LOG
from components import CharacterStats, CombatStats, Inventory, PermanentStats, TileMovement, Wallet
from ui_sizes import UI

# Nomes amigáveis opcionais — mapas sem entrada aqui usam o nome do arquivo
_DEBUG_MAP_NAMES: dict[str, str] = {
    "maps/map_1.csv":           "Mapa Principal",
    "maps/map_cave_west.csv":   "Caverna Oeste",
    "maps/map_cave_east.csv":   "Caverna Leste",
}

class DebugHandlers:
    # ------------------------------------------------------------------
    # Debug modal (F12)
    # ------------------------------------------------------------------

    def _debug_levelup(self, n: int) -> None:
        """Sobe n níveis instantaneamente, concedendo 1 ponto de talento por nível."""
        from stats_system import process_levelups
        from components import TalentTree
        cs   = self.world.get_component(self.player_entity, CharacterStats)
        comb = self.world.get_component(self.player_entity, CombatStats)
        perm = self.world.get_component(self.player_entity, PermanentStats)
        tt   = self.world.get_component(self.player_entity, TalentTree)
        if not cs or not comb:
            return
        # Força XP suficiente para n level-ups e processa via função centralizada
        for _ in range(n):
            cs.current_xp = cs.xp_to_next_level
            process_levelups(self.world, self.player_entity, cs, comb, perm)
        LOG.add(f"[DEBUG] Nivel {cs.level} — {tt.available_points if tt else 0} pontos de talento.", (120, 200, 255))

    def _handle_debug_click(self, event) -> None:
        px, py = self._safe_panel_origin(UI.DEBUG_W, UI.DEBUG_H)
        px, py = px + UI.DEBUG_OFFSET_X, py + UI.DEBUG_OFFSET_Y
        PW, PH = self._u(UI.DEBUG_W), self._u(UI.DEBUG_H)
        mx, my = event.pos

        # Botão fechar (X) — botão esquerdo
        if event.button == 1:
            close_r = pygame.Rect(px + PW - self._u(44), py + self._u(8), self._u(36), self._u(36))
            if close_r.collidepoint(mx, my):
                self._show_debug = False
                return

        # Clique fora fecha
        if not pygame.Rect(px, py, PW, PH).collidepoint(mx, my):
            self._show_debug = False
            return

        # Troca de aba (botão esquerdo)
        if event.button == 1:
            for rect, tab_id in self._debug_tab_buttons:
                if rect.collidepoint(mx, my):
                    self._debug_tab = tab_id
                    return

            if self._debug_tab == "nivel":
                for rect, n in self._debug_buttons:
                    if rect.collidepoint(mx, my):
                        self._debug_levelup(n)
                        return
                if self._debug_hud_toggle_r and self._debug_hud_toggle_r.collidepoint(mx, my):
                    self._hud_show_debug_stats = not self._hud_show_debug_stats
                    return

            elif self._debug_tab == "ouro":
                for rect, amount in self._debug_gold_buttons:
                    if rect.collidepoint(mx, my):
                        self._debug_add_gold(amount)
                        return

            elif self._debug_tab == "mapa":
                for rect, map_file in self._debug_map_buttons:
                    if rect.collidepoint(mx, my):
                        self._debug_open_map(map_file)
                        return

        # Aba de itens: clique direito = adicionar ao inventário
        elif event.button == 3 and self._debug_tab == "itens":
            for rect, factory in self._debug_item_buttons:
                if rect.collidepoint(mx, my):
                    self._debug_add_item(factory)
                    return

    def _debug_add_gold(self, amount: int) -> None:
        wallet = self.world.get_component(self.player_entity, Wallet)
        if wallet:
            wallet.gold += amount
            LOG.add(f"[DEBUG] +{amount}g adicionado. Total: {wallet.gold}g", (120, 200, 255))

    def _debug_add_item(self, factory) -> None:
        inv = self.world.get_component(self.player_entity, Inventory)
        if not inv:
            return
        if len(inv.items) >= inv.max_slots:
            LOG.add("[DEBUG] Mochila cheia!", (220, 100, 50))
            return
        item = factory()
        inv.items.append(item)
        LOG.add(f"[DEBUG] {item.name} adicionado a mochila.", (120, 200, 255))

    def _debug_open_map(self, map_file: str) -> None:
        """Carrega map_file no overlay e abre para o jogador clicar o destino de teleporte."""
        from map_loader import load_map_csv
        terrain_matrix, _, __, ___ = load_map_csv(map_file)
        player_tm = self.world.get_component(self.player_entity, TileMovement)
        ptx = player_tm.current_tile_x if player_tm else 0
        pty = player_tm.current_tile_y if player_tm else 0
        self._map_overlay.load_map(terrain_matrix, map_file)
        self._map_overlay.set_active_map(map_file)
        self._minimap.on_map_load()
        self._map_overlay.toggle(ptx, pty)
        self._debug_teleport_map = map_file
        self._show_debug = False

    def _get_debug_item_catalog(self) -> list:
        """Retorna lista de (name, rarity, factory) de todos os itens do jogo. Cache lazy."""
        if self._debug_item_catalog:
            return self._debug_item_catalog
        from loot_tables import _T
        from merchant_data import SHOPS
        rarity_order = {"common": 0, "uncommon": 1, "rare": 2, "epic": 3}
        catalog = []
        for factory in _T.values():
            sample = factory()
            catalog.append((sample.name, sample.rarity, factory))
        seen = set(n for n, _, _ in catalog)
        for shop in SHOPS.values():
            for entry in shop["stock"]:
                sample = entry["factory"]()
                if sample.name not in seen:
                    seen.add(sample.name)
                    catalog.append((sample.name, sample.rarity, entry["factory"]))
        catalog.sort(key=lambda x: (rarity_order.get(x[1], 0), x[0]))
        self._debug_item_catalog = catalog
        return catalog

    def _draw_debug_modal(self) -> None:
        """Desenha o painel de debug (F12) com abas: Nivel / Itens / Ouro."""
        from components import TalentTree
        cs = self.world.get_component(self.player_entity, CharacterStats)
        tt = self.world.get_component(self.player_entity, TalentTree)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not cs:
            return

        px, py = self._safe_panel_origin(UI.DEBUG_W, UI.DEBUG_H)
        px, py = px + UI.DEBUG_OFFSET_X, py + UI.DEBUG_OFFSET_Y
        PW, PH = self._u(UI.DEBUG_W), self._u(UI.DEBUG_H)
        mx, my = pygame.mouse.get_pos()

        font_md = self.font_md
        font_sm = self.font_sm
        font_lg = self.font_lg

        # Fundo
        bg = pygame.Surface((PW, PH), pygame.SRCALPHA)
        bg.fill((10, 8, 5, 238))
        self.screen.blit(bg, (px, py))
        pygame.draw.rect(self.screen, (100, 80, 50), (px, py, PW, PH), 2)

        # Título
        title = font_lg.render("DEBUG  [F12]", True, (120, 200, 255))
        self.screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + self._u(10)))

        # Botão fechar
        close_r = pygame.Rect(px + PW - self._u(44), py + self._u(8), self._u(36), self._u(36))
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (180, 60, 60) if close_hov else (80, 30, 30),
                         close_r, border_radius=3)
        xs = font_md.render("X", True, (255, 255, 255))
        self.screen.blit(xs, (close_r.centerx - xs.get_width() // 2,
                              close_r.centery - xs.get_height() // 2))

        pygame.draw.line(self.screen, (80, 65, 40),
                         (px + self._u(4), py + self._u(52)), (px + PW - self._u(4), py + self._u(52)))

        # --- Abas ---
        TAB_DEFS = [("nivel", "Nivel"), ("itens", "Itens"), ("ouro", "Ouro"), ("mapa", "Mapa")]
        tab_w, tab_h, tab_gap = self._u(160), self._u(38), self._u(10)
        tabs_total_w = len(TAB_DEFS) * tab_w + (len(TAB_DEFS) - 1) * tab_gap
        tx0 = px + PW // 2 - tabs_total_w // 2
        ty0 = py + self._u(58)

        self._debug_tab_buttons = []
        for i, (tab_id, label) in enumerate(TAB_DEFS):
            tr = pygame.Rect(tx0 + i * (tab_w + tab_gap), ty0, tab_w, tab_h)
            self._debug_tab_buttons.append((tr, tab_id))
            is_active = self._debug_tab == tab_id
            tab_hov   = tr.collidepoint(mx, my)
            if is_active:
                col_bg, col_bord, col_txt = (40, 60, 110), (100, 160, 255), (200, 230, 255)
            elif tab_hov:
                col_bg, col_bord, col_txt = (35, 35, 50), (80, 100, 150), (180, 180, 220)
            else:
                col_bg, col_bord, col_txt = (20, 20, 28), (55, 50, 40), (110, 110, 120)
            pygame.draw.rect(self.screen, col_bg,   tr, border_radius=3)
            pygame.draw.rect(self.screen, col_bord, tr, 1, border_radius=3)
            lbl = font_md.render(label, True, col_txt)
            self.screen.blit(lbl, (tr.centerx - lbl.get_width() // 2,
                                   tr.centery - lbl.get_height() // 2))

        pygame.draw.line(self.screen, (80, 65, 40),
                         (px + self._u(4), py + self._u(104)), (px + PW - self._u(4), py + self._u(104)))

        content_y = py + self._u(114)

        if self._debug_tab == "nivel":
            self._draw_debug_tab_nivel(px, content_y, PW, font_md, font_sm, cs, tt, mx, my)
        elif self._debug_tab == "itens":
            self._draw_debug_tab_itens(px, content_y, PW, font_md, font_sm, mx, my)
        elif self._debug_tab == "ouro":
            self._draw_debug_tab_ouro(px, content_y, PW, font_md, font_sm, wallet, mx, my)
        elif self._debug_tab == "mapa":
            self._draw_debug_tab_mapa(px, content_y, PW, font_md, font_sm, mx, my)

        # Footer
        hint = font_sm.render("ESC para fechar", True, (80, 75, 60))
        self.screen.blit(hint, (px + PW // 2 - hint.get_width() // 2, py + PH - self._u(28)))

    def _draw_debug_tab_nivel(self, px, content_y, PW, font_md, font_sm, cs, tt, mx, my) -> None:
        points = tt.available_points if tt else 0
        for i, line in enumerate([
            f"Nivel atual:       {cs.level}",
            f"Pontos de talento: {points}",
        ]):
            surf = font_sm.render(line, True, (200, 190, 160))
            self.screen.blit(surf, (px + self._u(20), content_y + self._u(8) + i * self._u(22)))

        self._debug_buttons = []
        btn_labels = [("+1 nivel", 1), ("+5 niveis", 5), ("+10 niveis", 10), ("+50 niveis", 50)]
        btn_w, btn_h, gap = self._u(170), self._u(48), self._u(14)
        cols = 2
        bx0 = px + PW // 2 - (cols * btn_w + (cols - 1) * gap) // 2
        by0 = content_y + self._u(70)
        for i, (label, n) in enumerate(btn_labels):
            bx = bx0 + (i % cols) * (btn_w + gap)
            by = by0 + (i // cols) * (btn_h + gap)
            rect = pygame.Rect(bx, by, btn_w, btn_h)
            hov = rect.collidepoint(mx, my)
            pygame.draw.rect(self.screen, (60, 100, 160) if hov else (30, 50, 80),
                             rect, border_radius=4)
            pygame.draw.rect(self.screen, (80, 130, 200), rect, 1, border_radius=4)
            lbl = font_sm.render(label, True, (220, 220, 255))
            self.screen.blit(lbl, (bx + btn_w // 2 - lbl.get_width() // 2,
                                   by + btn_h // 2 - lbl.get_height() // 2))
            self._debug_buttons.append((rect, n))

        # Toggle: atributos brutos/ratings de combate no HUD permanente
        # (escondido por padrão — redundante com a aba Estatísticas do
        # Inventário). Linha extra abaixo da grade de botões de nível.
        toggle_w = cols * btn_w + (cols - 1) * gap
        toggle_h = self._u(40)
        toggle_y = by0 + 2 * (btn_h + gap)
        self._debug_hud_toggle_r = pygame.Rect(bx0, toggle_y, toggle_w, toggle_h)
        on = self._hud_show_debug_stats
        hov_t = self._debug_hud_toggle_r.collidepoint(mx, my)
        col_bg = (40, 110, 60) if on else ((50, 50, 50) if hov_t else (35, 35, 35))
        pygame.draw.rect(self.screen, col_bg, self._debug_hud_toggle_r, border_radius=4)
        pygame.draw.rect(self.screen, (100, 200, 130) if on else (90, 90, 90),
                         self._debug_hud_toggle_r, 1, border_radius=4)
        toggle_lbl = font_sm.render(
            f"Stats brutos no HUD: {'ON' if on else 'OFF'}", True,
            (200, 255, 210) if on else (200, 200, 200))
        self.screen.blit(toggle_lbl, (self._debug_hud_toggle_r.centerx - toggle_lbl.get_width() // 2,
                                      self._debug_hud_toggle_r.centery - toggle_lbl.get_height() // 2))

    def _draw_debug_tab_ouro(self, px, content_y, PW, font_md, font_sm, wallet, mx, my) -> None:
        gold = wallet.gold if wallet else 0
        gold_s = font_md.render(f"Ouro atual: {gold}g", True, (255, 215, 0))
        self.screen.blit(gold_s, (px + PW // 2 - gold_s.get_width() // 2, content_y + self._u(10)))

        self._debug_gold_buttons = []
        btn_labels = [("+10g", 10), ("+100g", 100), ("+1000g", 1000)]
        btn_w, btn_h, gap = self._u(180), self._u(54), self._u(18)
        total_w = len(btn_labels) * btn_w + (len(btn_labels) - 1) * gap
        bx0 = px + PW // 2 - total_w // 2
        by0 = content_y + self._u(60)
        for i, (label, amount) in enumerate(btn_labels):
            bx = bx0 + i * (btn_w + gap)
            rect = pygame.Rect(bx, by0, btn_w, btn_h)
            hov = rect.collidepoint(mx, my)
            pygame.draw.rect(self.screen, (60, 130, 60) if hov else (30, 65, 30),
                             rect, border_radius=4)
            pygame.draw.rect(self.screen, (80, 200, 80), rect, 1, border_radius=4)
            lbl = font_sm.render(label, True, (200, 255, 200))
            self.screen.blit(lbl, (bx + btn_w // 2 - lbl.get_width() // 2,
                                   by0 + btn_h // 2 - lbl.get_height() // 2))
            self._debug_gold_buttons.append((rect, amount))

    def _draw_debug_tab_mapa(self, px, content_y, PW, font_md, font_sm, mx, my) -> None:
        import glob as _glob
        csv_files = sorted(
            f.replace("\\", "/") for f in _glob.glob("maps/*.csv")
            if not f.replace("\\", "/").endswith(("_terrain.csv", "_objects.csv"))
        )
        all_maps  = [
            (f, _DEBUG_MAP_NAMES.get(f, f.replace("maps/", "").replace(".csv", "")))
            for f in csv_files
        ]
        hint = font_sm.render("Clique no mapa para abrir e depois clique dir. para teleportar", True, (90, 80, 60))
        self.screen.blit(hint, (px + PW // 2 - hint.get_width() // 2, content_y + self._u(4)))

        self._debug_map_buttons = []
        ROW_H, ROW_W = self._u(52), PW - self._u(80)
        rx0 = px + self._u(40)
        ry0 = content_y + self._u(28)
        for i, (map_file, map_name) in enumerate(all_maps):
            r = pygame.Rect(rx0, ry0 + i * (ROW_H + self._u(10)), ROW_W, ROW_H)
            is_current = map_file == self._current_map_file
            hov = r.collidepoint(mx, my)
            if hov:
                col_bg, col_bord = (50, 80, 50), (100, 220, 100)
            elif is_current:
                col_bg, col_bord = (28, 45, 28), (60, 140, 60)
            else:
                col_bg, col_bord = (22, 18, 10), (55, 50, 40)
            pygame.draw.rect(self.screen, col_bg,   r, border_radius=4)
            pygame.draw.rect(self.screen, col_bord, r, 1, border_radius=4)
            col_txt = (180, 255, 180) if hov else (200, 190, 160)
            lbl = font_md.render(map_name, True, col_txt)
            self.screen.blit(lbl, (r.x + self._u(16), r.centery - lbl.get_height() // 2 - self._u(6)))
            tag = "[mapa atual]" if is_current else map_file
            tag_s = font_sm.render(tag, True, (80, 160, 80) if is_current else (70, 65, 55))
            self.screen.blit(tag_s, (r.x + self._u(16), r.centery + self._u(4)))
            self._debug_map_buttons.append((r, map_file))

    def _draw_debug_tab_itens(self, px, content_y, PW, font_md, font_sm, mx, my) -> None:
        _RARITY_COLORS = {
            "common":   (200, 200, 200),
            "uncommon": ( 30, 200,  30),
            "rare":     ( 80, 140, 255),
            "epic":     (180,  50, 255),
        }
        catalog = self._get_debug_item_catalog()
        inv = self.world.get_component(self.player_entity, Inventory)
        inv_full = inv and len(inv.items) >= inv.max_slots

        hint_col = (150, 80, 80) if inv_full else (90, 80, 60)
        hint_txt = "Mochila cheia!" if inv_full else "Clique direito: adicionar a mochila"
        hint = font_sm.render(hint_txt, True, hint_col)
        self.screen.blit(hint, (px + PW // 2 - hint.get_width() // 2, content_y + self._u(2)))

        ROW_H, MAX_ROWS = self._u(36), 9
        LIST_W = PW - self._u(28)
        list_x = px + self._u(14)
        list_y = content_y + self._u(24)

        max_scroll = max(0, len(catalog) - MAX_ROWS)
        self._debug_item_scroll = min(self._debug_item_scroll, max_scroll)

        self._debug_item_buttons = []
        for i, (name, rarity, factory) in enumerate(catalog):
            vis_i = i - self._debug_item_scroll
            if not (0 <= vis_i < MAX_ROWS):
                continue
            ry = list_y + vis_i * ROW_H
            r  = pygame.Rect(list_x, ry, LIST_W - self._u(12), ROW_H - self._u(2))
            hov     = r.collidepoint(mx, my)
            rar_col = _RARITY_COLORS.get(rarity, (100, 100, 100))
            pygame.draw.rect(self.screen, (40, 35, 20) if hov else (22, 18, 10),
                             r, border_radius=3)
            pygame.draw.rect(self.screen, rar_col if hov else (50, 40, 25),
                             r, 1, border_radius=3)
            pygame.draw.circle(self.screen, rar_col, (r.x + self._u(12), r.centery), self._u(5))
            name_s = font_sm.render(name, True, rar_col)
            self.screen.blit(name_s, (r.x + self._u(26), r.centery - name_s.get_height() // 2))
            rar_s = font_sm.render(rarity.capitalize(), True, rar_col)
            self.screen.blit(rar_s, (r.right - rar_s.get_width() - self._u(6),
                                     r.centery - rar_s.get_height() // 2))
            self._debug_item_buttons.append((r, factory))

        # Scrollbar
        if len(catalog) > MAX_ROWS:
            sb_h = MAX_ROWS * ROW_H
            sb_x = list_x + LIST_W - self._u(10)
            th   = max(self._u(20), sb_h * MAX_ROWS // len(catalog))
            ty   = list_y + (sb_h - th) * self._debug_item_scroll // max(1, max_scroll)
            pygame.draw.rect(self.screen, (35, 30, 18), (sb_x, list_y, self._u(5), sb_h), border_radius=2)
            pygame.draw.rect(self.screen, (120, 100, 60), (sb_x, ty, self._u(5), th), border_radius=2)
