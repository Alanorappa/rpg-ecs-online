"""
habilidades_handlers.py — Mixin com o painel de Habilidades (tecla H):
lista scrollável de skills aprendidas com drag-and-drop para a hotbar,
o ghost de drag exibido sobre os slots, e a tela de loading exibida
enquanto aguarda LOGIN_OK do servidor. Separado de game.py para manter
GameEngine conciso. Esta classe NÃO deve ser instanciada diretamente —
ela é herdada por GameEngine, que fornece self.world, self.player_entity,
self.screen, self.font_*, self._net e os demais atributos referenciados
aqui.
"""
import pygame

from components import PlayerSkills
from icon_manager import ICONS


class HabilidadesHandlers:

    # ── Painel de Habilidades (H) ─────────────────────────────────────────────

    def _draw_habilidades_panel(self, events: list) -> None:
        """Modal central de habilidades — lista scrollável com descrição completa + drag para hotbar."""
        from skill_config import SKILL_CATALOG, NUM_SLOTS
        from components import TalentTree as _TT

        ps = self.world.get_component(self.player_entity, PlayerSkills)
        if not ps:
            return

        mx, my   = pygame.mouse.get_pos()
        clicked  = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        released = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in events)

        for e in events:
            if e.type == pygame.MOUSEWHEEL:
                self._hab_scroll = max(0, self._hab_scroll - e.y)

        # Coleta skills aprendidas
        avail: list[str] = [sid for sid in SKILL_CATALOG if sid in ps.learned_skill_ids]
        for s in ps.skills:
            if s and getattr(s, "talent_id", None) and s.skill_id not in avail:
                avail.append(s.skill_id)
        _tt = self.world.get_component(self.player_entity, _TT)
        if _tt:
            from talent_data import TALENTS as _TAL
            for tid, pts in _tt.allocated.items():
                t = _TAL.get(tid)
                if not t or not t.get("unlocks_skill"):
                    continue
                if pts >= t.get("unlock_at", t["max_points"]) and t["unlocks_skill"] not in avail:
                    avail.append(t["unlocks_skill"])

        # ── Geometria — modal centralizado ────────────────────────────────
        PW      = 720
        PH      = 560
        ppx     = self.screen.get_width()  // 2 - PW // 2
        ppy     = self.screen.get_height() // 2 - PH // 2
        ICON_SZ = 48
        ROW_H   = 80          # altura de cada linha (ícone + nome + descrição completa)
        LIST_X  = ppx + 12
        LIST_W  = PW - 24
        CONTENT_Y = ppy + 44  # abaixo do header
        FOOTER_H  = 28

        max_vis    = max(1, (PH - (CONTENT_Y - ppy) - FOOTER_H - 10) // ROW_H)
        max_scroll = max(0, len(avail) - max_vis)
        self._hab_scroll = min(self._hab_scroll, max_scroll)

        if self._show_habilidades:
            # Overlay escurecido
            ov = pygame.Surface((self.screen.get_width(), self.screen.get_height()), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 160))
            self.screen.blit(ov, (0, 0))

            # Fundo do modal
            pygame.draw.rect(self.screen, (26, 20, 10), (ppx, ppy, PW, PH), border_radius=8)
            pygame.draw.rect(self.screen, (90, 72, 44), (ppx, ppy, PW, PH), 2, border_radius=8)

            # Título
            ts = self.font_md.render("Habilidades", True, (225, 195, 110))
            self.screen.blit(ts, (ppx + PW // 2 - ts.get_width() // 2, ppy + 10))

            # Botão [X]
            cr = pygame.Rect(ppx + PW - 30, ppy + 8, 24, 24)
            pygame.draw.rect(self.screen, (80, 40, 30) if cr.collidepoint(mx, my) else (50, 30, 20),
                             cr, border_radius=4)
            pygame.draw.rect(self.screen, (180, 80, 60), cr, 1, border_radius=4)
            xs = self.font_md.render("X", True, (220, 120, 100))
            self.screen.blit(xs, xs.get_rect(center=cr.center))
            if clicked and cr.collidepoint(mx, my):
                self._show_habilidades = False
                return

            pygame.draw.line(self.screen, (70, 56, 32),
                             (ppx + 12, ppy + 38), (ppx + PW - 12, ppy + 38))

            # ── Lista de skills ──────────────────────────────────────────
            vis = avail[self._hab_scroll: self._hab_scroll + max_vis]
            for idx, sid in enumerate(vis):
                ry    = CONTENT_Y + idx * ROW_H
                r     = pygame.Rect(LIST_X, ry, LIST_W, ROW_H - 4)
                is_src = (self._hab_drag_skill == sid)
                hov    = r.collidepoint(mx, my) and not self._hab_drag_skill
                alt    = idx % 2 == 1

                bg = (16, 12, 6) if is_src else ((46, 38, 22) if hov else (32, 26, 14) if alt else (26, 20, 10))
                pygame.draw.rect(self.screen, bg, r, border_radius=5)
                pygame.draw.rect(self.screen, (120, 98, 52) if hov else (62, 50, 30),
                                 r, 1, border_radius=5)

                # Ícone
                ic_key = ICONS.skill_key_by_name(f"skill_{sid}") or ICONS.skill_key(idx)
                ic     = ICONS.get(ic_key, ICON_SZ)
                if ic:
                    self.screen.blit(ic, (LIST_X + 8, ry + (ROW_H - 4 - ICON_SZ) // 2))

                entry    = SKILL_CATALOG.get(sid)
                name_txt = entry["name"] if isinstance(entry, dict) else sid
                desc_txt = entry.get("desc", "")   if isinstance(entry, dict) else ""
                cd       = entry.get("cooldown", 0) if isinstance(entry, dict) else 0
                cast_t   = entry.get("cast_time", 0) if isinstance(entry, dict) else 0

                tx = LIST_X + ICON_SZ + 20

                # Nome
                self.screen.blit(self.font_sm.render(name_txt, True, (230, 210, 148)),
                                 (tx, ry + 6))

                # Descrição completa — quebrada em duas linhas se necessário
                max_chars = (LIST_W - ICON_SZ - 28) // 7  # aprox chars por linha a font_xs
                if len(desc_txt) > max_chars:
                    cut = desc_txt.rfind(" ", 0, max_chars) or max_chars
                    line1, line2 = desc_txt[:cut], desc_txt[cut:].strip()
                else:
                    line1, line2 = desc_txt, ""
                self.screen.blit(self.font_xs.render(line1, True, (155, 140, 95)),
                                 (tx, ry + 28))
                if line2:
                    self.screen.blit(self.font_xs.render(line2, True, (155, 140, 95)),
                                     (tx, ry + 44))

                # Metadados (CD / cast) — canto direito da linha
                meta_parts = []
                if cd:
                    meta_parts.append(f"CD {cd:.0f}s")
                if cast_t:
                    meta_parts.append(f"Cast {cast_t:.1f}s")
                if meta_parts:
                    meta_s = self.font_xs.render("  ·  ".join(meta_parts), True, (110, 100, 65))
                    self.screen.blit(meta_s, (LIST_X + LIST_W - meta_s.get_width() - 10,
                                              ry + ROW_H - meta_s.get_height() - 8))

                if clicked and r.collidepoint(mx, my):
                    self._hab_drag_skill = sid

            # Barra de scroll
            if len(avail) > max_vis:
                list_h = max_vis * ROW_H
                pct    = self._hab_scroll / max(1, max_scroll)
                bar_h  = max(20, list_h * max_vis // max(1, len(avail)))
                bar_y  = CONTENT_Y + int((list_h - bar_h) * pct)
                bar_x  = ppx + PW - 8
                pygame.draw.rect(self.screen, (44, 35, 18), (bar_x, CONTENT_Y, 5, list_h), border_radius=2)
                pygame.draw.rect(self.screen, (125, 100, 55), (bar_x, bar_y, 5, bar_h), border_radius=2)

            # Dica de rodapé
            hint = self.font_xs.render(
                "Clique e arraste uma habilidade para um slot da hotbar  |  H ou [X] para fechar",
                True, (90, 82, 55))
            self.screen.blit(hint, hint.get_rect(centerx=ppx + PW // 2, y=ppy + PH - 20))

        # Ghost de drag renderizado separadamente (veja _draw_hab_drag_ghost),
        # APÓS o redesenho da hotbar, para ficar sempre na frente dos slots.

        # ── Drop sobre a hotbar ────────────────────────────────────────────
        if released and self._hab_drag_skill:
            sid     = self._hab_drag_skill
            self._hab_drag_skill = None
            # Calcula posições de TODOS os 10 slots (incluindo vazios) para detecção
            total_w = NUM_SLOTS * self._HB_W + (NUM_SLOTS - 1) * self._HB_PAD
            x0      = self.screen.get_width() // 2 - total_w // 2
            y0      = self.screen.get_height() - self._HB_H - 10
            for i in range(NUM_SLOTS):
                sx = x0 + i * (self._HB_W + self._HB_PAD)
                if pygame.Rect(sx, y0, self._HB_W, self._HB_H).collidepoint(mx, my):
                    # Verifica se a skill já está em outro slot (swap)
                    ex = next((k for k in range(NUM_SLOTS)
                               if ps.skills[k] and ps.skills[k].skill_id == sid), None)
                    if ex is not None and ex != i:
                        ps.skills[ex], ps.skills[i] = ps.skills[i], ps.skills[ex]
                    elif ex is None:
                        new_s = type(ps)._make_skill(sid, SKILL_CATALOG)
                        if new_s is None:
                            new_s = next((s for s in ps.skills if s and s.skill_id == sid), None)
                        if new_s:
                            ps.skills[i] = new_s
                    self._save_config()
                    break

    def _draw_loading_screen(self, dt: float) -> None:
        """Tela de loading exibida enquanto aguarda LOGIN_OK do servidor."""
        self._loading_anim_t += dt
        dots = "." * (int(self._loading_anim_t * 2) % 4)

        sw, sh = self.screen.get_width(), self.screen.get_height()
        self.screen.fill((8, 6, 4))

        # Gradiente sutil no centro
        _grad = pygame.Surface((sw, sh), pygame.SRCALPHA)
        _grad.fill((0, 0, 0, 0))
        for _r in range(min(sw, sh) // 2, 0, -20):
            _alpha = max(0, 40 - _r // 8)
            pygame.draw.circle(_grad, (30, 20, 10, _alpha), (sw // 2, sh // 2), _r)
        self.screen.blit(_grad, (0, 0))

        # Título
        _title = self.font_lg.render("RPG Online", True, (220, 185, 80))
        self.screen.blit(_title, _title.get_rect(centerx=sw // 2, centery=sh // 2 - 60))

        # Linha decorativa
        pygame.draw.line(self.screen, (80, 62, 30),
                         (sw // 2 - 120, sh // 2 - 32),
                         (sw // 2 + 120, sh // 2 - 32), 1)

        # Status
        if self._net and self._net.connected:
            msg = f"Carregando personagem{dots}"
        elif self._net and getattr(self._net, "reconnecting", False):
            msg = f"Reconectando{dots}"
        else:
            msg = f"Conectando ao servidor{dots}"
        _status = self.font_sm.render(msg, True, (150, 130, 70))
        self.screen.blit(_status, _status.get_rect(centerx=sw // 2, centery=sh // 2 + 10))

        # Barra de progresso linear: 0% no início → 100% quando loading_min_t zera.
        # _loading_min_t começa em 1.5 e é decrementado a cada frame.
        _BAR_W, _BAR_H = 400, 10
        _bx = sw // 2 - _BAR_W // 2
        _by = sh // 2 + 32
        _pct  = max(0.0, min(1.0, 1.0 - self._loading_min_t / 1.5))
        _fill = max(1, int(_BAR_W * _pct))
        pygame.draw.rect(self.screen, (70, 15, 15),  (_bx, _by, _BAR_W, _BAR_H), border_radius=3)
        pygame.draw.rect(self.screen, (25, 90, 25),  (_bx, _by, _fill,  _BAR_H), border_radius=3)
        pygame.draw.rect(self.screen, (50, 35, 20),  (_bx, _by, _BAR_W, _BAR_H), 1, border_radius=3)

        # Dica de timeout restante (só aparece nos últimos 5s)
        if self._loading_timeout < 5.0:
            _hint = self.font_xs.render(
                f"Sem resposta do servidor — continuando em {self._loading_timeout:.0f}s...",
                True, (120, 80, 60))
            self.screen.blit(_hint, _hint.get_rect(centerx=sw // 2, centery=sh // 2 + 55))

    def _draw_hab_drag_ghost(self) -> None:
        """Renderiza o ícone fantasma que segue o mouse durante o drag do painel H.
        Chamado APÓS _draw_hotbar() para ficar sempre na frente dos slots."""
        if not self._hab_drag_skill:
            return
        GSZ   = 48
        mx, my = pygame.mouse.get_pos()
        ghost = pygame.Surface((GSZ, GSZ), pygame.SRCALPHA)
        ghost.fill((30, 24, 12, 180))
        ic_k = ICONS.skill_key_by_name(f"skill_{self._hab_drag_skill}") or ICONS.skill_key(0)
        ic   = ICONS.get(ic_k, GSZ - 4)
        if ic:
            ghost.blit(ic, (2, 2))
        self.screen.blit(ghost, ghost.get_rect(center=(mx, my)))
