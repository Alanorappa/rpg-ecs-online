"""
death_ui_handlers.py — Mixin com a UI do fluxo de morte/espírito (ghost):
janela "Você morreu" (botão "Liberar espírito"), HUD do espírito (contador
de revive automático no cemitério) e prompt "Reviver agora?" perto do corpo.
Separado de game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.world, self.player_entity, self.screen, self.font_sm/font_md e os
demais atributos referenciados aqui.
"""
import pygame

from components import GhostState
from client.colors import C_WHITE, C_YELLOW, C_RED, C_GREEN
from shared.constants import GHOST_GRAVEYARD_REVIVE_S


class DeathUIHandlers:

    _DEATH_MODAL_DELAY_S = 2.0

    # ------------------------------------------------------------------
    def _update_death_ui(self, events: list, dt: float) -> None:
        gst = self.world.get_component(self.player_entity, GhostState)
        if gst is None:
            return

        if gst.is_dead and not gst.is_ghost:
            self._death_timer += dt
        else:
            self._death_timer = 0.0

        if gst.is_ghost:
            self._ghost_timer += dt
        else:
            self._ghost_timer = 0.0

        for ev in events:
            if ev.type != pygame.MOUSEBUTTONDOWN or ev.button != 1:
                continue
            mx, my = ev.pos

            if (gst.is_dead and not gst.is_ghost
                    and self._death_timer >= self._DEATH_MODAL_DELAY_S
                    and self._death_release_btn
                    and self._death_release_btn.collidepoint(mx, my)):
                self._net.release_spirit()

            if (gst.is_ghost and gst.near_corpse
                    and self._ghost_revive_btn
                    and self._ghost_revive_btn.collidepoint(mx, my)):
                self._net.revive_request()

    # ------------------------------------------------------------------
    def _render_death_ui(self) -> None:
        gst = self.world.get_component(self.player_entity, GhostState)
        if gst is None:
            return

        if gst.is_dead and not gst.is_ghost and self._death_timer >= self._DEATH_MODAL_DELAY_S:
            self._render_death_modal()
        elif gst.is_ghost:
            self._render_ghost_hud(gst)

    # ------------------------------------------------------------------
    def _render_death_modal(self) -> None:
        surf = self.screen
        SW, SH = surf.get_size()
        mx, my = pygame.mouse.get_pos()

        panel_w, panel_h = 360, 160
        px = (SW - panel_w) // 2
        py = (SH - panel_h) // 2

        panel_r = pygame.Rect(px, py, panel_w, panel_h)
        pygame.draw.rect(surf, (20, 10, 10), panel_r, border_radius=6)
        pygame.draw.rect(surf, (140, 30, 30), panel_r, 2, border_radius=6)

        title_s = self.font_md.render("Você morreu", True, C_RED)
        surf.blit(title_s, (px + (panel_w - title_s.get_width()) // 2, py + 24))

        btn_w, btn_h = 200, 44
        btn_r = pygame.Rect(px + (panel_w - btn_w) // 2, py + panel_h - btn_h - 24, btn_w, btn_h)
        hov = btn_r.collidepoint(mx, my)
        pygame.draw.rect(surf, (60, 40, 40) if hov else (40, 25, 25), btn_r, border_radius=4)
        pygame.draw.rect(surf, C_RED, btn_r, 2, border_radius=4)
        label_s = self.font_sm.render("Liberar espírito", True, C_WHITE)
        surf.blit(label_s, (btn_r.centerx - label_s.get_width() // 2,
                             btn_r.centery - label_s.get_height() // 2))
        self._death_release_btn = btn_r

    # ------------------------------------------------------------------
    def _render_ghost_hud(self, gst: GhostState) -> None:
        surf = self.screen
        SW, SH = surf.get_size()
        mx, my = pygame.mouse.get_pos()
        self._death_release_btn = None

        remaining = max(0.0, GHOST_GRAVEYARD_REVIVE_S - self._ghost_timer)
        txt = f"Espírito — revive automático em {int(remaining)}s"
        info_s = self.font_sm.render(txt, True, C_YELLOW)
        surf.blit(info_s, ((SW - info_s.get_width()) // 2, 20))

        self._ghost_revive_btn = None
        if gst.near_corpse:
            panel_w, panel_h = 280, 120
            px = (SW - panel_w) // 2
            py = (SH - panel_h) // 2

            panel_r = pygame.Rect(px, py, panel_w, panel_h)
            pygame.draw.rect(surf, (10, 20, 10), panel_r, border_radius=6)
            pygame.draw.rect(surf, C_GREEN, panel_r, 2, border_radius=6)

            title_s = self.font_md.render("Reviver agora?", True, C_WHITE)
            surf.blit(title_s, (px + (panel_w - title_s.get_width()) // 2, py + 16))

            btn_w, btn_h = 100, 40
            btn_r = pygame.Rect(px + (panel_w - btn_w) // 2, py + panel_h - btn_h - 16, btn_w, btn_h)
            hov = btn_r.collidepoint(mx, my)
            pygame.draw.rect(surf, (40, 70, 40) if hov else (25, 45, 25), btn_r, border_radius=4)
            pygame.draw.rect(surf, C_GREEN, btn_r, 2, border_radius=4)
            label_s = self.font_sm.render("Sim", True, C_WHITE)
            surf.blit(label_s, (btn_r.centerx - label_s.get_width() // 2,
                                 btn_r.centery - label_s.get_height() // 2))
            self._ghost_revive_btn = btn_r
