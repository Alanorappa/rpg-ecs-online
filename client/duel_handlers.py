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

from ui import atlas_window as aw
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
        # que o servidor liberou em _duel_pairs. O resolver em si é o
        # composto ÚNICO registrado em game.py::_client_pvp_context (duelo
        # OU zona PvP) — aqui só atualiza o estado que ele lê
        # (_duel_opponent_local_eid); desregistrar não é mais necessário,
        # o composto já volta a False quando o duelo acaba.
        from ui.floating_text import WARN
        WARN.add(f"Duelo contra {opp_name}!")

    def _handle_msg_duel_end(self, payload: dict) -> None:
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
            # Anúncio "fulano venceu beltrano" vai pra aba Local via
            # CHAT_MESSAGE broadcast do servidor (server/session.py) —
            # assim quem está por perto também vê, não só os duelistas.
            WARN.add("Você venceu o duelo" if winner == self._my_eid
                     else "Você foi derrotado")
        elif reason == "declined":
            WARN.add("Duelo recusado.")
        elif reason == "distance":
            WARN.add("Duelo cancelado: muito longe.")
        elif reason == "disconnect":
            WARN.add("Duelo cancelado: oponente saiu.")
        elif reason:                      # "invalid" e afins do request
            WARN.add("Duelo indisponível.")

    # ── Modal de convite (atlas_window.prompt — mesmo do trade/grupo) ────────
    # Ações no SOLTAR, dentro do desenho; _handle_duel_click só "segura" o
    # clique enquanto o convite está aberto (modal bloqueante).

    def _draw_duel_ui(self) -> None:
        if self._duel_invite_from is None:
            return
        self._duel_hits = {}
        fired = aw.prompt(self.screen, getattr(self, "_ui_events", []), area=self._mm_safe_area(),
                          press=self._trade_press, font=self.font_md,
                          message=f"{self._duel_invite_from[1]} te desafiou para um duelo!",
                          buttons=["Aceitar", "Recusar"], hits=self._duel_hits, hit_prefix="duel")
        if fired is None:
            return
        from shared.messages import MsgType
        if self._net:
            self._net.send(MsgType.DUEL_ACCEPT if fired == 0 else MsgType.DUEL_DECLINE, {})
        self._duel_invite_from_val = None
        SOUNDS.play_ui("button_click")

    def _handle_duel_click(self, event) -> bool:
        """True = clique consumido pelo modal de convite de duelo (bloqueante)."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        return self._duel_invite_from is not None
