"""
client/portal_handlers.py — Mixin do GameEngine: portais com seleção
de destino (30/09/2026, pedido do usuário).

Diferente das transições de mapa existentes (cavernas,
`self.transition_tiles`, `game.py::_build_transition_tiles`) — que
auto-disparam ZONE_CHANGE_REQ assim que o jogador PISA no tile, com
destino FIXO — um portal tem vários destinos possíveis: pisar no tile
só abre um modal de seleção (`self.portal_tiles`, dado 100% local,
mesmo princípio de `transition_tiles`, sem round-trip de rede pra
listar opções). Ao escolher um destino, manda PORTAL_ENTER_REQ; o
servidor valida o destino contra a lista DAQUELE portal (nunca confia
em to_map/target_x/y soltos, ver server/portal_processor.py) e inicia
um cast de alguns segundos — a teleportação de verdade só acontece
quando ESSE cast completa no servidor, via o ZONE_CHANGE normal que já
existe (`_handle_msg_zone_change`/`_do_transition`, zero código novo
de carregamento de mapa).

Sem lógica de gameplay aqui além da PREVISÃO local (barra de cast
otimista, animação disparada só na confirmação real do servidor,
cancelamento local imediato ao se mover) — mesmo espírito preditivo-
mas-servidor-manda de `SpellCast`/`ui/spell_system.py`. Esta classe NÃO
deve ser instanciada diretamente — é herdada por GameEngine, que
fornece self.world, self.player_entity, self.screen, self._net,
self.font_sm/font_md, self._u() (UIScaleMixin) e self._any_modal_open()
(ModalStackHandlers).
"""
import pygame

from ui.sound_manager import SOUNDS

_MODAL_W = 340
_ROW_H = 44
_PAD = 16

# Duração usada SÓ pra previsão otimista local (barra de cast aparece
# na hora do clique, antes do servidor confirmar) — o valor REAL vem
# de PORTAL_CAST_START logo em seguida e sobrescreve este palpite.
# Espelha server/world_server.py::WorldServer.PORTAL_CAST_DURATION_S —
# se um dia divergir, o servidor sempre vence (é só o "chute" inicial).
_DEFAULT_PORTAL_CAST_DURATION_S = 5.0


class PortalHandlers:

    # ── Estado (lazy — GameEngine não precisa de __init__ extra, mesmo
    # padrão de client/battleground_handlers.py::_bg_result) ────────────────

    @property
    def portal_tiles(self) -> dict:
        """(tile_x, tile_y) -> dict do portal (id, x, y, destinations) —
        populado por `_build_portal_tiles`, 100% local (mesmo dado que
        `self.transition_tiles`, carregado do mapa, nunca via rede)."""
        return getattr(self, "_portal_tiles_val", None) or {}

    @portal_tiles.setter
    def portal_tiles(self, value: dict) -> None:
        self._portal_tiles_val = value

    @property
    def _portal_modal_open(self):
        """None fora do modal de seleção; senão o dict do portal atual."""
        return getattr(self, "_portal_modal_open_val", None)

    @_portal_modal_open.setter
    def _portal_modal_open(self, value) -> None:
        self._portal_modal_open_val = value

    @property
    def _portal_modal_dismissed_key(self):
        """(x,y) do portal cujo modal foi fechado manualmente enquanto o
        jogador ainda estava em cima — evita reabrir sozinho todo frame
        até ele sair do tile e voltar."""
        return getattr(self, "_portal_modal_dismissed_key_val", None)

    @_portal_modal_dismissed_key.setter
    def _portal_modal_dismissed_key(self, value) -> None:
        self._portal_modal_dismissed_key_val = value

    @property
    def _portal_cast_active(self):
        """None sem cast em andamento; senão {"elapsed","duration",
        "portal_id","dest_index"} — lido por client/hud_handlers.py::
        _draw_cast_bar."""
        return getattr(self, "_portal_cast_active_val", None)

    @_portal_cast_active.setter
    def _portal_cast_active(self, value) -> None:
        self._portal_cast_active_val = value

    # ── Construção de dados do mapa (chamado de game.py, ao lado de
    # _build_transition_tiles) ────────────────────────────────────────────

    def _build_portal_tiles(self, spawn_points: dict) -> None:
        self.portal_tiles = {
            (gate["x"], gate["y"]): gate
            for gate in spawn_points.get("portal_gates", [])
        }
        self._portal_modal_open = None
        self._portal_modal_dismissed_key = None
        self._portal_cast_active = None

    # ── Update por frame (chamado de game.py, perto da detecção de
    # transição de cavernas) ──────────────────────────────────────────────

    def _update_portal_state(self, dt: float) -> None:
        from engine.components import TileMovement as _TMPortalUp
        tm = self.world.get_component(self.player_entity, _TMPortalUp)

        if self._portal_cast_active is not None:
            # Movimento cancela o cast LOCALMENTE na hora (feedback
            # imediato) — o servidor manda PORTAL_CAST_CANCEL também
            # (mesmo espírito de SpellCast.interruptible), mas não
            # precisamos esperar a rede pra já parar de mostrar a barra.
            if tm is not None and tm.is_moving:
                self._cancel_portal_cast_local()
            else:
                self._portal_cast_active["elapsed"] += dt
            return

        if not self.portal_tiles or tm is None or tm.is_moving:
            return
        key = (tm.current_tile_x, tm.current_tile_y)
        if key not in self.portal_tiles:
            self._portal_modal_dismissed_key = None
            return
        if key == self._portal_modal_dismissed_key:
            return
        if self._portal_modal_open is not None or self._any_modal_open():
            return
        self._portal_modal_open = self.portal_tiles[key]
        SOUNDS.play_ui("map_open")

    def _cancel_portal_cast_local(self) -> None:
        self._portal_cast_active = None
        from engine.components import TileMovement as _TMPortalC
        tm = self.world.get_component(self.player_entity, _TMPortalC)
        if tm is not None and tm.anim_attack_state == "portal":
            tm.anim_attack_state = ""

    # ── Handlers de rede (dispatch em client/network_handlers.py) ────────────

    def _handle_msg_portal_cast_start(self, payload: dict) -> None:
        """Servidor confirmou o cast (request_portal_enter aceito) —
        substitui a previsão otimista (_DEFAULT_PORTAL_CAST_DURATION_S)
        pela duração REAL, e SÓ AGORA dispara a animação do personagem
        (nunca client-side otimista puro, mesmo princípio de
        trigger_attack_anim só rodar em confirmação real)."""
        duration = payload.get("duration", _DEFAULT_PORTAL_CAST_DURATION_S)
        self._portal_cast_active = {
            "elapsed":    0.0,
            "duration":   duration,
            "portal_id":  payload.get("portal_id", ""),
            "dest_index": payload.get("dest_index", -1),
        }
        from engine.components import TileMovement as _TMPortalStart
        import time as _t_portal_start
        tm = self.world.get_component(self.player_entity, _TMPortalStart)
        if tm is not None:
            # Estado "portal" — engine/character_animation.py aceita
            # QUALQUER string aqui, cascata de fallback já cobre sprite
            # ausente (cai pra idle sozinho): zero mudança de código lá.
            # Convenção de arquivo: {class_id}_portal_{direction}.png.
            tm.anim_attack_state          = "portal"
            tm.anim_attack_started_ms     = int(_t_portal_start.time() * 1000)
            tm.anim_attack_frame_ms       = 0  # 0 = usa DEFAULT_FRAME_MS (ver character_animation.py)
            tm.anim_attack_loop_duration_ms = int(duration * 1000)

    def _handle_msg_portal_cast_cancel(self, payload: dict) -> None:
        """Servidor cancelou o cast (moveu, ou qualquer motivo futuro) —
        idempotente: se o cliente já tinha cancelado localmente (caso
        comum, o cancelamento local roda 1 frame antes da confirmação
        de rede chegar), isto só confirma o estado que já era."""
        self._cancel_portal_cast_local()

    # ── Envio ao servidor ─────────────────────────────────────────────────

    def _send_portal_enter(self, gate: dict, dest_index: int) -> None:
        if not self._net:
            return
        from shared.messages import MsgType as _MTPortal
        self._net.send(_MTPortal.PORTAL_ENTER_REQ, {
            "portal_id":  gate["id"],
            "dest_index": dest_index,
        })
        # Previsão otimista da barra — CONFIRMADA (duração real) por
        # PORTAL_CAST_START logo em seguida.
        self._portal_cast_active = {
            "elapsed":    0.0,
            "duration":   _DEFAULT_PORTAL_CAST_DURATION_S,
            "portal_id":  gate["id"],
            "dest_index": dest_index,
        }
        self._portal_modal_dismissed_key = (gate["x"], gate["y"])
        self._portal_modal_open = None
        SOUNDS.play_ui("button_click")

    # ── Modal de seleção de destino ───────────────────────────────────────

    def _close_portal_modal(self) -> None:
        gate = self._portal_modal_open
        if gate is not None:
            self._portal_modal_dismissed_key = (gate["x"], gate["y"])
        self._portal_modal_open = None

    def _portal_modal_rects(self, gate: dict) -> "tuple[pygame.Rect, list[pygame.Rect]]":
        """(panel_rect, [rect por destino]) — geometria compartilhada
        entre desenho e clique (mesmo princípio de outros modais do
        projeto, ex: hotbar/_trade_layout)."""
        destinations = gate.get("destinations", [])
        modal_w = self._u(_MODAL_W)
        row_h   = self._u(_ROW_H)
        pad     = self._u(_PAD)
        title_h = self._u(40)
        modal_h = title_h + pad + len(destinations) * row_h + pad
        sw, sh  = self.screen.get_size()
        panel = pygame.Rect(sw // 2 - modal_w // 2, sh // 2 - modal_h // 2, modal_w, modal_h)
        rects = []
        for i in range(len(destinations)):
            rects.append(pygame.Rect(
                panel.x + pad, panel.y + title_h + pad + i * row_h,
                modal_w - pad * 2, row_h - self._u(6),
            ))
        return panel, rects

    def _draw_portal_modal(self) -> None:
        gate = self._portal_modal_open
        if gate is None:
            return
        destinations = gate.get("destinations", [])
        panel, rects = self._portal_modal_rects(gate)

        overlay = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        self.screen.blit(overlay, (0, 0))
        pygame.draw.rect(self.screen, (18, 22, 34), panel, border_radius=8)
        pygame.draw.rect(self.screen, (90, 130, 180), panel, 2, border_radius=8)

        title_s = self.font_md.render("Selecione o destino", False, (210, 225, 245))
        self.screen.blit(title_s, (panel.centerx - title_s.get_width() // 2,
                                   panel.y + self._u(12)))

        mouse_pos = pygame.mouse.get_pos()
        for dest, rect in zip(destinations, rects):
            hov = rect.collidepoint(mouse_pos)
            pygame.draw.rect(self.screen, (55, 75, 105) if hov else (35, 45, 65),
                             rect, border_radius=5)
            pygame.draw.rect(self.screen, (120, 160, 210), rect, 1, border_radius=5)
            label_s = self.font_sm.render(dest.get("label", "?"), False, (225, 235, 250))
            self.screen.blit(label_s, (rect.x + self._u(10),
                                       rect.centery - label_s.get_height() // 2))

    def _handle_portal_click(self, event) -> bool:
        """True sempre que o modal está aberto (bloqueante — consome
        qualquer clique, mesmo padrão de _handle_bg_result_click)."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        gate = self._portal_modal_open
        if gate is None:
            return False
        _panel, rects = self._portal_modal_rects(gate)
        for i, rect in enumerate(rects):
            if rect.collidepoint(event.pos):
                self._send_portal_enter(gate, i)
                return True
        return True
