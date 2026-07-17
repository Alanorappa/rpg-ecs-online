"""
party_handlers.py — Mixin do GameEngine: lado cliente do grupo (party —
ver server/party_processor.py e ARQUITETURA_ONLINE.md §34.19).

Espelha o padrão do duelo (client/duel_handlers.py): modal de convite
central (Aceitar/Recusar), estado mínimo no próprio GameEngine. Convite
sai por DUAS vias que convergem no mesmo PARTY_INVITE:
  1. Botão "Convidar p/ Grupo" do modal de interação (client/
     trade_handlers.py::_send_party_invite_request).
  2. Comando de chat "/convidar Nome" (_try_handle_party_chat_command
     abaixo, chamado por client/chat_handlers.py::_send_chat_message
     ANTES de mandar como texto normal).

HP dos membros no frame só atualiza em tempo real pra quem está visível
no seu AOI (igual qualquer player remoto já funciona) — membro fora de
alcance mostra o último HP conhecido do PARTY_STATE. Sincronização de
HP independente de distância é limitação conhecida desta leva (ver
plano/ARQUITETURA_ONLINE.md).
"""
import pygame

from ui.ui_sizes import UI
from ui.sound_manager import SOUNDS


class PartyHandlers:

    # ── Estado (lazy — GameEngine não precisa de __init__ extra) ─────────────

    @property
    def _party_invite_from(self):
        return getattr(self, "_party_invite_from_val", None)   # (eid, name) | None

    @property
    def _party_id(self) -> int:
        return getattr(self, "_party_id_val", -1)

    @property
    def _party_leader_eid(self) -> int:
        return getattr(self, "_party_leader_eid_val", -1)

    @property
    def _party_members(self) -> list:
        return getattr(self, "_party_members_val", [])   # [{eid,name,class_id,level,hp,hp_max}]

    # ── Handlers de rede (dispatch em client/network_handlers.py) ────────────

    def _handle_msg_party_invite_received(self, payload: dict) -> None:
        self._party_invite_from_val = (payload.get("from_eid", -1),
                                       payload.get("from_name", "?"))
        SOUNDS.play_ui("button_click")

    def _handle_msg_party_invite_failed(self, payload: dict) -> None:
        reason = payload.get("reason", "")
        from ui.floating_text import WARN
        _msgs = {
            "invalid":  "Convite de grupo indisponível.",
            "declined": "Convite de grupo recusado.",
        }
        WARN.add(_msgs.get(reason, "Convite de grupo indisponível."))

    def _handle_msg_party_state(self, payload: dict) -> None:
        self._party_id_val          = payload.get("party_id", -1)
        self._party_leader_eid_val  = payload.get("leader_eid", -1)
        self._party_members_val     = payload.get("members", [])

    # ── Comando de chat "/convidar Nome" ─────────────────────────────────────

    def _try_handle_party_chat_command(self, text: str) -> bool:
        """Chamado por client/chat_handlers.py::_send_chat_message ANTES
        de mandar como chat normal. True = era um comando (consumido,
        não vira mensagem de chat), False = texto normal, segue o fluxo
        de sempre."""
        _stripped = text.strip()
        _lower = _stripped.lower()
        _prefix = None
        for _p in ("/convidar ", "/invite "):
            if _lower.startswith(_p):
                _prefix = _p
                break
        if _prefix is None:
            return False

        name = _stripped[len(_prefix):].strip()
        from ui.floating_text import WARN
        if not name:
            WARN.add("Uso: /convidar NomeDoJogador")
            return True

        from engine.components import RemoteControlled as _RCcmd
        target_local = -1
        for eid, rc in self.world.get_entities_with(_RCcmd):
            if rc.name.lower() == name.lower():
                target_local = eid
                break
        if target_local == -1:
            WARN.add(f"Jogador '{name}' não encontrado (precisa estar visível).")
            return True

        self._send_party_invite_request(target_local)
        return True

    def _send_party_leave(self) -> None:
        if self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.PARTY_LEAVE, {})

    def _send_party_kick(self, target_server_eid: int) -> None:
        if self._net:
            from shared.messages import MsgType
            self._net.send(MsgType.PARTY_KICK, {"target_eid": target_server_eid})

    # ── Modal de convite (espelho do _draw_duel_ui) ───────────────────────────

    def _party_invite_button_rects(self):
        SW, SH = self.screen.get_size()
        w, h = self._u(UI.TRADE_INVITE_W), self._u(UI.TRADE_INVITE_H)
        x0, y0 = (SW - w) // 2, (SH - h) // 2
        btn_w = (w - self._u(30)) // 2
        accept_r  = pygame.Rect(x0 + self._u(10), y0 + h - self._u(48), btn_w, self._u(36))
        decline_r = pygame.Rect(x0 + w - btn_w - self._u(10), y0 + h - self._u(48), btn_w, self._u(36))
        return pygame.Rect(x0, y0, w, h), accept_r, decline_r

    def _draw_party_invite_ui(self) -> None:
        if self._party_invite_from is None:
            return
        rect, accept_r, decline_r = self._party_invite_button_rects()
        SW, SH = self.screen.get_size()
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        self.screen.blit(overlay, (0, 0))
        pygame.draw.rect(self.screen, (24, 26, 20), rect, border_radius=8)
        pygame.draw.rect(self.screen, (150, 160, 90), rect, 2, border_radius=8)
        msg = f"{self._party_invite_from[1]} te convidou pro grupo!"
        msg_s = self.font_md.render(msg, False, (225, 230, 200))
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

    def _handle_party_click(self, event) -> bool:
        """True = clique consumido pela UI de grupo (convite ou frame)."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        if self._party_invite_from is not None:
            _, accept_r, decline_r = self._party_invite_button_rects()
            from shared.messages import MsgType
            mx, my = event.pos
            if accept_r.collidepoint(mx, my):
                if self._net:
                    self._net.send(MsgType.PARTY_ACCEPT, {})
                self._party_invite_from_val = None
                SOUNDS.play_ui("button_click")
            elif decline_r.collidepoint(mx, my):
                if self._net:
                    self._net.send(MsgType.PARTY_DECLINE, {})
                self._party_invite_from_val = None
                SOUNDS.play_ui("button_click")
            return True   # modal é bloqueante: consome qualquer clique

        return self._click_party_frame(event)

    # ── Frame de grupo (lista simples: nome + nível + barra de HP) ───────────

    _FRAME_W  = 150
    _FRAME_H  = 30
    _FRAME_GAP = 4
    _FRAME_X0  = 10
    _FRAME_Y0  = 80   # abaixo da zona/coordenadas (canto sup. direito é delas)

    def _party_frame_rects(self) -> list:
        """[(member_dict, row_rect, leave_or_kick_rect)] — só monta se
        houver grupo. O botão da linha é "Sair" pra mim mesmo, "Expulsar"
        pros outros SE eu for líder (senão a linha não tem botão)."""
        members = self._party_members
        if not members:
            return []
        w, h, gap = self._u(self._FRAME_W), self._u(self._FRAME_H), self._u(self._FRAME_GAP)
        x0, y0 = self._u(self._FRAME_X0), self._u(self._FRAME_Y0)
        i_am_leader = self._party_leader_eid == self._my_eid
        out = []
        for i, m in enumerate(members):
            row = pygame.Rect(x0, y0 + i * (h + gap), w, h)
            btn = None
            is_me = m["eid"] == self._my_eid
            if is_me or i_am_leader:
                btn = pygame.Rect(row.right - self._u(22), row.y + self._u(4),
                                  self._u(18), self._u(18))
            out.append((m, row, btn))
        return out

    def _draw_party_frames(self) -> None:
        rows = self._party_frame_rects()
        if not rows:
            return
        for member, row, btn in rows:
            pygame.draw.rect(self.screen, (22, 22, 26), row, border_radius=4)
            pygame.draw.rect(self.screen, (90, 90, 100), row, 1, border_radius=4)
            ratio = 0.0
            if member.get("hp_max", 0) > 0:
                ratio = max(0.0, min(1.0, member["hp"] / member["hp_max"]))
            bar_w = int((row.w - self._u(8)) * ratio)
            bar_rect = pygame.Rect(row.x + self._u(4), row.bottom - self._u(8),
                                   max(0, bar_w), self._u(5))
            pygame.draw.rect(self.screen, (200, 60, 60), bar_rect)
            label = f"{member['name']} [{member['level']}]"
            lbl_s = self.font_xs.render(label, False, (225, 225, 225))
            self.screen.blit(lbl_s, (row.x + self._u(4), row.y + self._u(3)))
            if btn is not None:
                is_me = member["eid"] == self._my_eid
                pygame.draw.rect(self.screen, (90, 50, 50) if not is_me else (60, 60, 70),
                                 btn, border_radius=3)
                x_s = self.font_xs.render("X", False, (230, 210, 210))
                self.screen.blit(x_s, (btn.centerx - x_s.get_width() // 2,
                                      btn.centery - x_s.get_height() // 2))

    def _click_party_frame(self, event) -> bool:
        rows = self._party_frame_rects()
        if not rows:
            return False
        mx, my = event.pos
        for member, row, btn in rows:
            if btn is not None and btn.collidepoint(mx, my):
                if member["eid"] == self._my_eid:
                    self._send_party_leave()
                else:
                    self._send_party_kick(member["eid"])
                SOUNDS.play_ui("button_click")
                return True
            if row.collidepoint(mx, my):
                return True   # consome o clique mesmo sem botão (linha do frame)
        return False
