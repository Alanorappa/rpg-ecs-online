"""
duel_handlers.py — Mixin do GameEngine: lado cliente do duelo (contexto
PvP por convite — ver server/duel_processor.py e ARQUITETURA_ONLINE.md).

Espelha o padrão do trade (client/trade_handlers.py): modal de convite
central (Aceitar/Recusar), estado mínimo no próprio GameEngine, e o
contexto PvP client-side registrado SÓ enquanto o duelo dura — é ele que
faz clique direito/skills funcionarem contra o oponente (o gate de
can_engage em ui/systems.py passa a liberar aquele alvo específico) e o
nameplate dele ficar vermelho (_draw_remote_players consulta
_duel_opponent_local_eid).
"""
import pygame

from ui.ui_sizes import UI
from ui.sound_manager import SOUNDS


class DuelHandlers:

    # ── Estado (lazy — GameEngine não precisa de __init__ extra) ─────────────

    @property
    def _duel_invite_from(self):
        return getattr(self, "_duel_invite_from_val", None)   # (eid, name) | None

    @property
    def _duel_opponent_local_eid(self) -> int:
        return getattr(self, "_duel_opponent_local_val", -1)

    # ── Handlers de rede (dispatch em client/network_handlers.py) ────────────

    def _handle_msg_duel_invite(self, payload: dict) -> None:
        self._duel_invite_from_val = (payload.get("from_eid", -1),
                                      payload.get("from_name", "?"))
        SOUNDS.play_ui("button_click")

    def _handle_msg_duel_start(self, payload: dict) -> None:
        opp_server_eid = payload.get("opponent_eid", -1)
        opp_name       = payload.get("opponent_name", "?")
        opp_local      = self._remote_players.get(opp_server_eid, -1)
        self._duel_opponent_local_val = opp_local
        # Contexto PvP client-side: libera can_engage SÓ contra o oponente
        # (clique direito ataca, skills miram, SPACE engaja) — espelho do
        # que o servidor liberou em _duel_pairs. Desregistrado no DUEL_END.
        from engine.faction_system import register_pvp_context

        def _duel_ctx(_w, a, b):
            pair = {self.player_entity, self._duel_opponent_local_eid}
            return self._duel_opponent_local_eid != -1 and {a, b} == pair

        register_pvp_context(_duel_ctx)
        from ui.floating_text import WARN
        WARN.add(f"Duelo contra {opp_name}!")

    def _handle_msg_duel_end(self, payload: dict) -> None:
        from engine.faction_system import register_pvp_context
        register_pvp_context(None)
        self._duel_opponent_local_val = -1
        self._duel_invite_from_val    = None
        # Oponente deixa de ser alvo válido: limpa target/perseguição locais
        # (o servidor já recusa qualquer ataque novo — isto é só UX).
        from engine.components import CombatState as _CSde
        cst = self.world.get_component(self.player_entity, _CSde)
        if cst is not None and cst.target_entity_id != -1:
            cst.is_pursuing = False
        reason = payload.get("reason", "")
        winner = payload.get("winner_eid", -1)
        from ui.floating_text import WARN
        if reason == "win":
            WARN.add("Vitória no duelo!" if winner == self._my_eid
                     else "Derrota no duelo!")
        elif reason == "declined":
            WARN.add("Duelo recusado.")
        elif reason == "distance":
            WARN.add("Duelo cancelado: muito longe.")
        elif reason == "disconnect":
            WARN.add("Duelo cancelado: oponente saiu.")
        elif reason:                      # "invalid" e afins do request
            WARN.add("Duelo indisponível.")

    # ── Modal de convite (espelho do _draw_trade_invite) ─────────────────────

    def _duel_invite_button_rects(self):
        SW, SH = self.screen.get_size()
        w, h = self._u(UI.TRADE_INVITE_W), self._u(UI.TRADE_INVITE_H)
        x0, y0 = (SW - w) // 2, (SH - h) // 2
        btn_w = (w - self._u(30)) // 2
        accept_r  = pygame.Rect(x0 + self._u(10), y0 + h - self._u(48), btn_w, self._u(36))
        decline_r = pygame.Rect(x0 + w - btn_w - self._u(10), y0 + h - self._u(48), btn_w, self._u(36))
        return pygame.Rect(x0, y0, w, h), accept_r, decline_r

    def _draw_duel_ui(self) -> None:
        if self._duel_invite_from is None:
            return
        rect, accept_r, decline_r = self._duel_invite_button_rects()
        SW, SH = self.screen.get_size()
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        self.screen.blit(overlay, (0, 0))
        pygame.draw.rect(self.screen, (32, 20, 18), rect, border_radius=8)
        pygame.draw.rect(self.screen, (180, 90, 70), rect, 2, border_radius=8)
        msg = f"{self._duel_invite_from[1]} te desafiou para um duelo!"
        msg_s = self.font_md.render(msg, False, (235, 215, 200))
        self.screen.blit(msg_s, (rect.x + (rect.w - msg_s.get_width()) // 2,
                                 rect.y + self._u(30)))
        pygame.draw.rect(self.screen, (50, 90, 50), accept_r, border_radius=5)
        pygame.draw.rect(self.screen, (90, 50, 50), decline_r, border_radius=5)
        a_s = self.font_sm.render("Aceitar", False, (220, 240, 220))
        d_s = self.font_sm.render("Recusar", False, (240, 220, 220))
        self.screen.blit(a_s, (accept_r.centerx - a_s.get_width() // 2,
                               accept_r.centery - a_s.get_height() // 2))
        self.screen.blit(d_s, (decline_r.centerx - d_s.get_width() // 2,
                               decline_r.centery - d_s.get_height() // 2))

    def _handle_duel_click(self, event) -> bool:
        """True = clique consumido pelo modal de convite de duelo."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        if self._duel_invite_from is None:
            return False
        _, accept_r, decline_r = self._duel_invite_button_rects()
        from shared.messages import MsgType
        mx, my = event.pos
        if accept_r.collidepoint(mx, my):
            if self._net:
                self._net.send(MsgType.DUEL_ACCEPT, {})
            self._duel_invite_from_val = None
            SOUNDS.play_ui("button_click")
        elif decline_r.collidepoint(mx, my):
            if self._net:
                self._net.send(MsgType.DUEL_DECLINE, {})
            self._duel_invite_from_val = None
            SOUNDS.play_ui("button_click")
        return True   # modal é bloqueante: consome qualquer clique
