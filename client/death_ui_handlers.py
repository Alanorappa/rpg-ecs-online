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

from engine.components import GhostState
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
        # Respawn automático (BG, dungeon) — decidido pelo servidor no
        # momento da morte (RespawnMixin._handle_player_death) e ecoado no
        # payload de PLAYER_DEATH pra GhostState.auto_respawn (07/10/2026,
        # ver CLAUDE.md). NUNCA checar InstanceInventoryUIState.active aqui
        # — isso é progressão normalizada (zera equipamento/nível, só BG
        # usa), um conceito DIFERENTE de "este conteúdo revive sozinho".
        # Antes desta correção o modal usava aquela flag como proxy pra "é
        # BG" — funcionava pra BG mas zeraria o personagem real de um
        # jogador de dungeon se reusada ali sem pensar.
        gst = self.world.get_component(self.player_entity, GhostState)
        if gst is not None and gst.auto_respawn:
            self._render_death_modal_auto_respawn(gst)
            return

        surf = self.screen
        SW, SH = surf.get_size()
        mx, my = pygame.mouse.get_pos()

        panel_w, panel_h = 360, 160
        px = (SW - panel_w) // 2
        py = (SH - panel_h) // 2

        panel_r = pygame.Rect(px, py, panel_w, panel_h)
        pygame.draw.rect(surf, (20, 10, 10), panel_r, border_radius=6)
        pygame.draw.rect(surf, (140, 30, 30), panel_r, 2, border_radius=6)

        title_s = self.font_md.render("Você morreu", False, C_RED)
        surf.blit(title_s, (px + (panel_w - title_s.get_width()) // 2, py + 24))

        btn_w, btn_h = 200, 44
        btn_r = pygame.Rect(px + (panel_w - btn_w) // 2, py + panel_h - btn_h - 24, btn_w, btn_h)
        hov = btn_r.collidepoint(mx, my)
        pygame.draw.rect(surf, (60, 40, 40) if hov else (40, 25, 25), btn_r, border_radius=4)
        pygame.draw.rect(surf, C_RED, btn_r, 2, border_radius=4)
        label_s = self.font_sm.render("Liberar espírito", False, C_WHITE)
        surf.blit(label_s, (btn_r.centerx - label_s.get_width() // 2,
                             btn_r.centery - label_s.get_height() // 2))
        self._death_release_btn = btn_r

    # ------------------------------------------------------------------
    def _render_death_modal_auto_respawn(self, gst: GhostState) -> None:
        """Variante do modal de morte pra conteúdo com respawn AUTOMÁTICO
        (BG, dungeon — servidor já cuida do revive de verdade, ver
        BgQueueProcessorMixin._tick_bg_respawns/DungeonProcessorMixin.
        _tick_dungeon_respawns), então não há botão "Liberar espírito" aqui,
        só a contagem regressiva. `gst.respawn_s` (07/10/2026, ver CLAUDE.md)
        é a duração que ESTE conteúdo usa — não mais hardcoded pra
        DEBUG_BG_RESPAWN_S, cada instância manda a própria. `self._death_timer`
        (já acumulado por _update_death_ui desde que gst.is_dead virou True)
        é a MESMA base de tempo que o servidor usa — nenhuma mensagem nova
        precisa sincronizar isso."""
        surf = self.screen
        SW, SH = surf.get_size()

        panel_w, panel_h = 360, 160
        px = (SW - panel_w) // 2
        py = (SH - panel_h) // 2

        panel_r = pygame.Rect(px, py, panel_w, panel_h)
        pygame.draw.rect(surf, (20, 10, 10), panel_r, border_radius=6)
        pygame.draw.rect(surf, (140, 30, 30), panel_r, 2, border_radius=6)

        title_s = self.font_md.render("Você morreu", False, C_RED)
        surf.blit(title_s, (px + (panel_w - title_s.get_width()) // 2, py + 24))

        remaining = max(0, int(gst.respawn_s - self._death_timer + 0.999))
        info_s = self.font_sm.render(f"Respawn em {remaining}s", False, C_YELLOW)
        surf.blit(info_s, (px + (panel_w - info_s.get_width()) // 2, py + 92))

        self._death_release_btn = None  # sem botão — respawn é automático

    # ------------------------------------------------------------------
    def _render_ghost_hud(self, gst: GhostState) -> None:
        surf = self.screen
        SW, SH = surf.get_size()
        mx, my = pygame.mouse.get_pos()
        self._death_release_btn = None

        remaining = max(0.0, GHOST_GRAVEYARD_REVIVE_S - self._ghost_timer)
        txt = f"Espírito — revive automático em {int(remaining)}s"
        info_s = self.font_sm.render(txt, False, C_YELLOW)
        surf.blit(info_s, ((SW - info_s.get_width()) // 2, 20))

        self._ghost_revive_btn = None
        if gst.near_corpse:
            panel_w, panel_h = 280, 120
            px = (SW - panel_w) // 2
            py = (SH - panel_h) // 2

            panel_r = pygame.Rect(px, py, panel_w, panel_h)
            pygame.draw.rect(surf, (10, 20, 10), panel_r, border_radius=6)
            pygame.draw.rect(surf, C_GREEN, panel_r, 2, border_radius=6)

            title_s = self.font_md.render("Reviver agora?", False, C_WHITE)
            surf.blit(title_s, (px + (panel_w - title_s.get_width()) // 2, py + 16))

            btn_w, btn_h = 100, 40
            btn_r = pygame.Rect(px + (panel_w - btn_w) // 2, py + panel_h - btn_h - 16, btn_w, btn_h)
            hov = btn_r.collidepoint(mx, my)
            pygame.draw.rect(surf, (40, 70, 40) if hov else (25, 45, 25), btn_r, border_radius=4)
            pygame.draw.rect(surf, C_GREEN, btn_r, 2, border_radius=4)
            label_s = self.font_sm.render("Sim", False, C_WHITE)
            surf.blit(label_s, (btn_r.centerx - label_s.get_width() // 2,
                                 btn_r.centery - label_s.get_height() // 2))
            self._ghost_revive_btn = btn_r
