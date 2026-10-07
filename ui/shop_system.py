# shop_system.py
"""
ShopSystem — painel de comerciante NPC (clique direito abre; painel
esquerdo compra do mercador, painel direito vende da mochila do
jogador). Extraído de ui/systems.py (18/09/2026, ver
PROBLEMAS_ARQUITETURA.md §185, item #2 do backlog de 14).
"""
from __future__ import annotations
import pygame
# TODO(B4): _font é usado apenas em sistemas visuais (HUD, FloatingText). O servidor
# importa `from systems import EnemyAISystem` e acaba carregando Pygame fonts pelo
# caminho. Funciona com SDL_VIDEODRIVER=dummy, mas é dependência desnecessária.
# Fix futuro: mover sistemas visuais para ClientSystems ou usar lazy import em render().
from ui.fonts import make as _font
from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI

# Re-exporta apply_effect de core_systems para compatibilidade com todo o código
# que já faz `from systems import apply_effect`.
from engine.core_systems import apply_effect
try:
    from debug.mob_combat_debug import MCL as _MCL
except ImportError:
    _MCL = None  # não disponível no ambiente do cliente

from engine.components import Position, Renderable, TileMovement, PlayerAutoMove, Inventory, Equipment, Wallet, Merchant, CharacterStats, NPC, NPCInteractionRequest
from engine.world import World
from engine.tileset import TILE_SIZE
from engine.utils import chebyshev, get_camera_offset
# Hoisted pro topo (14/09/2026, revisão de performance — ver
# arquitetura/CLIENT_PERFORMANCE_REVIEW.md item 2): estavam sendo
# reimportados dentro de RenderSystem.render(), no loop de desenho mais
# quente do cliente (uma vez por ENTIDADE visível, todo frame). Módulos
# puros (`from __future__ import annotations` só), sem ciclo de import
# com ui/systems.py — seguro no topo.
from ui.combat_log import LOG
from ui.icon_manager import ICONS
from ui.ui_helpers import item_tooltip_lines, fill_surf
from ui.npc_interaction import walk_to_npc, suppress_chase
from content.merchant_data import SHOPS, INSTANCE_SHOP_STOCK_BY_CLASS
from content.item_table import RARITY_COLORS as _SHARED_RARITY_COLORS
from engine.quest_events import fire as quest_fire
import engine.quest_logic as quest_logic

# Lookup reverso skill_id → (talent_id, min_points) — fonte única em
# world_systems (_TALENT_SKILL_REQS, usado também por is_skill_authorized
# no gate autoritativo do servidor). Alias mantido pro gate de UI local.



# ---------------------------------------------------------------------------
# Sistemas headless de gameplay - movidos para world_systems.py (problema F).
# Re-export para compatibilidade: todo codigo cliente continua fazendo
# `from systems import EnemyAISystem` etc. Codigo de SERVIDOR deve importar
# de world_systems diretamente (nao arrasta pygame).
# ---------------------------------------------------------------------------
from engine.world_systems import System, EnemyAISystem


class ShopSystem(UIScaleMixin, System):
    """
    Sistema de comerciantes NPC.
    - Clique direito no NPC → abre painel de loja.
    - Painel esquerdo: itens à venda (clique direito = comprar).
    - Painel direito: mochila do jogador (clique direito = vender).
    - Botão [↩ Desfazer] fica sempre renderizado desabilitado (histórico de
      transação nunca é populado — servidor é autoritativo). Já era assim
      antes desta limpeza (07/08/2026, Fase 2); mantido intacto — remoção
      da UI é decisão de produto, não decidida aqui. Ver PROBLEMAS_ARQUITETURA.md.
    """

    _FONT_BASES = {"_font_sm": 22, "_font_md": 30, "_font_lg": 38}

    PANEL_W       = UI.SHOP_W
    PANEL_H       = UI.SHOP_H
    ROW_H         = UI.SHOP_ROW_H
    ICON_S        = UI.SHOP_ICON_SZ
    MAX_ROWS      = UI.SHOP_MAX_ROWS
    LEFT_W        = UI.SHOP_LEFT_W
    RIGHT_W       = UI.SHOP_RIGHT_W
    GAP           = UI.SHOP_GAP
    BODY_Y_OFFSET = UI.SHOP_BODY_Y_OFFSET   # distância do topo do painel até a primeira linha de item
    FOOTER_H      = UI.SHOP_FOOTER_H        # altura reservada para ouro + dica no rodapé
    SELL_RATIO    = 0.4
    MAX_HISTORY   = 20

    # Fonte única em content/item_table.py (achado 06 do benchmark
    # arquitetural, PROBLEMAS_ARQUITETURA.md §12/§13 — estava duplicado
    # literalmente aqui e mais abaixo neste mesmo arquivo).
    _RARITY_COLORS = _SHARED_RARITY_COLORS

    def __init__(self, world: World, player_entity: int, screen):
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self.world_surf = screen
        self.hud_surf   = screen
        # open_merchant_id → ShopUIState component (via property abaixo)
        self._pending_merchant_id: int   = -1   # aguardando jogador chegar
        self._right_click_consumed: bool = False
        self.transaction_history: list = []
        self._shop_scroll: int = 0
        self._bag_scroll:  int = 0
        # Online: injetado pelo GameEngine. None = modo offline (lógica local)
        self._net = None
        self.pending_tooltip = None
        self._open_cooldown: float = 0.0  # impede compra/venda logo após abrir a loja
        # Modal de quantidade (Shift+clique direito em item stackável)
        self._qty_modal: dict | None = None  # None = fechado

        SW, SH = screen.get_size()

    @property
    def open_merchant_id(self) -> int:
        from ui.ui_components import ShopUIState
        ui = self.world.get_component(self.player_entity, ShopUIState)
        return ui.open_merchant_id if ui else -1

    @open_merchant_id.setter
    def open_merchant_id(self, value: int) -> None:
        from ui.ui_components import ShopUIState
        ui = self.world.get_component(self.player_entity, ShopUIState)
        if ui:
            ui.open_merchant_id = value

    @property
    def is_open(self) -> bool:
        return self.open_merchant_id != -1

    @property
    def qty_modal_open(self) -> bool:
        return self._qty_modal is not None

    def open_for(self, merchant_eid: int) -> None:
        """Abre a loja para o merchant_eid especificado, resetando estado interno."""
        self.open_merchant_id   = merchant_eid
        self._pending_merchant_id = -1
        self._shop_scroll       = 0
        self._bag_scroll        = 0
        self._open_cooldown     = 0.3

    def _panel_origin(self):
        x0, y0 = self._safe_panel_origin(self.PANEL_W, self.PANEL_H)
        return x0 + UI.SHOP_OFFSET_X, y0 + UI.SHOP_OFFSET_Y

    def _get_cam(self):
        SW = self.world_surf.get_width()
        SH = self.world_surf.get_height()
        return get_camera_offset(self.world, SW, SH)

    def _try_open_merchant(self, eid: int) -> None:
        """Abre a loja, OU — se travada por quest (04/10/2026, pedido do
        usuário; trava generalizada por NPC individual em 06/10/2026,
        ver `NPCUnlock`/`quest_logic.is_npc_unlocked`) — pede pro
        QuestDialogSystem abrir no lugar, mesmo `NPCInteractionRequest`
        que BlacksmithSystem já usa pra "abrir outro System de NPC" (ver
        docstring do componente). Sem nenhuma mensagem de bloqueio de
        propósito: o player só vê o fluxo de quest normal — igual
        qualquer NPC que só tem quest pra oferecer ainda. `ui/
        trainer_system.py::_open_menu`/`ui/crafting_system.py::
        _open_menu` já fazem exatamente essa priorização; aqui replica
        pra loja, que não tem menu de assunto intermediário (abre
        direto)."""
        from engine.components import QuestLog as _QL_shop
        ql = self.world.get_component(self.player_entity, _QL_shop)
        if quest_logic.is_npc_unlocked(self.world, eid, ql):
            self.open_merchant_id     = eid
            self._pending_merchant_id = -1
            self._shop_scroll = 0
            self._bag_scroll  = 0
            self._open_cooldown = 0.5
            _m = self.world.get_component(eid, Merchant)
            if _m:
                _npc = self.world.get_component(eid, NPC)
                quest_fire("talk_to_npc", npc_name=_npc.name if _npc else "Comerciante")
        else:
            self._pending_merchant_id = -1
            self.world.add_component(self.player_entity, NPCInteractionRequest(
                kind="open_quest_dialog", target_eid=eid))
            self._open_cooldown = 0.3

    # ------------------------------------------------------------------
    # Update — detecção de clique no mundo
    # ------------------------------------------------------------------

    def update(self, events=None, dt: float = 0) -> None:
        # Pedido de abertura vindo de outro System (ex: BlacksmithSystem
        # "Negociar") — 15/09/2026, item 4 da revisão de performance
        # (ver docstring de NPCInteractionRequest, engine/components.py).
        # Só remove o componente quando o `kind` é DESTE system — outro
        # `kind` (ex: "open_quest_dialog") fica intocado pro dono real
        # consumir no MESMO frame, sem depender de ordem de update() entre
        # Systems.
        _npc_req = self.world.get_component(self.player_entity, NPCInteractionRequest)
        if _npc_req is not None and _npc_req.kind == "open_shop":
            # _try_open_merchant (não open_for direto) — respeita a trava
            # de quest mesmo vindo de outro System (ex: "Negociar" do
            # BlacksmithSystem), 04/10/2026.
            self._try_open_merchant(_npc_req.target_eid)
            self.world.remove_component(self.player_entity, NPCInteractionRequest)

        self._right_click_consumed = False
        if self._open_cooldown > 0:
            self._open_cooldown = max(0.0, self._open_cooldown - dt)

        # --- Verifica se o jogador chegou perto do merchant pendente ---
        if self._pending_merchant_id != -1:
            mt = self._merchant_tile(self._pending_merchant_id)
            pt = self._player_tile()
            if mt is None:
                # Merchant não existe mais
                self._pending_merchant_id = -1
            elif pt and self._cheby(pt, mt) <= 1:
                # Chegou — para o auto-move e abre a loja (ou redireciona
                # pra quest, se travada — ver _try_open_merchant)
                auto = self.world.get_component(self.player_entity, PlayerAutoMove)
                if auto:
                    auto.active        = False
                    auto.path          = []
                    auto.ground_target = None
                _arrived_eid = self._pending_merchant_id
                self._try_open_merchant(_arrived_eid)

        if not events:
            return

        cam_x, cam_y = self._get_cam()

        for event in events:
            if event.type == pygame.MOUSEWHEEL and self.is_open and self._qty_modal is None:
                x0, y0  = self._panel_origin()
                mx, _my = pygame.mouse.get_pos()
                mid_x   = x0 + self.GAP + self.LEFT_W
                if mx < mid_x:
                    self._shop_scroll = max(0, self._shop_scroll - event.y)
                else:
                    self._bag_scroll  = max(0, self._bag_scroll  - event.y)

            elif (event.type == pygame.MOUSEBUTTONDOWN
                  and event.button == 3
                  and not self.is_open):
                mx, my = event.pos
                _sc = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
                wx, wy = mx * _sc + cam_x, my * _sc + cam_y
                for eid, pos, rend, _ in self.world.get_entities_with(
                        Position, Renderable, Merchant):
                    hw = rend.width  / 2
                    hh = rend.height / 2
                    if abs(wx - pos.x) <= hw and abs(wy - pos.y) <= hh:
                        pt = self._player_tile()
                        mt = self._merchant_tile(eid)
                        if pt and mt and self._cheby(pt, mt) <= 1:
                            # Já adjacente — abre direto (ou redireciona
                            # pra quest, se travada — _try_open_merchant)
                            suppress_chase(self.world, self.player_entity)
                            self._try_open_merchant(eid)
                        else:
                            # Inicia caminhada até tile adjacente
                            self._pending_merchant_id = eid
                            walk_to_npc(self.world, self.player_entity, pt, mt)
                        self._right_click_consumed = True
                        break

    # ------------------------------------------------------------------
    # Transações
    # ------------------------------------------------------------------

    def _sell_price(self, item) -> int:
        return max(1, int(item.value * self.SELL_RATIO))

    def _buy(self, entry: dict, shop_id: str = "") -> None:
        """Compra 1 unidade. Envia BUY_REQUEST ao servidor (autoritativo) —
        UI atualizada quando BUY_RESULT chegar."""
        if not self._net or not shop_id:
            return
        from shared.messages import MsgType as _MTShop
        _wallet_buy = self.world.get_component(self.player_entity, Wallet)
        preview = entry["factory"]()
        self._net.send(_MTShop.BUY_REQUEST, {
            "shop_id":     shop_id,
            "item_name":   preview.name,
            "item_id":     getattr(preview, "item_id", ""),
            "quantity":    1,
            "current_gold": _wallet_buy.gold if _wallet_buy else 0,
            "npc_eid":     self.open_merchant_id,
        })

    def _buy_qty(self, entry: dict, qty: int, shop_id: str = "") -> None:
        """Compra qty unidades de um item stackável. Envia BUY_REQUEST."""
        if not self._net or not shop_id:
            return
        from shared.messages import MsgType as _MTShop
        _wallet_qty = self.world.get_component(self.player_entity, Wallet)
        preview = entry["factory"]()
        self._net.send(_MTShop.BUY_REQUEST, {
            "shop_id":     shop_id,
            "item_name":   preview.name,
            "item_id":     getattr(preview, "item_id", ""),
            "quantity":    qty,
            "current_gold": _wallet_qty.gold if _wallet_qty else 0,
            "npc_eid":     self.open_merchant_id,
        })

    def _open_qty_modal(self, entry: dict, shop_id: str = "") -> None:
        """Abre o modal de seleção de quantidade para um item stackável."""
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not inv or not wallet:
            return
        price   = entry["price"]
        preview = entry["factory"]()
        # Máximo limitado por ouro e por espaço de stack disponível
        max_by_gold  = wallet.gold // max(1, price)
        existing_cap = sum(
            (it.max_stack - it.stack)
            for it in inv.items
            if it is not None and it.item_id == preview.item_id and it.stack < it.max_stack
        )
        free_slots  = inv.max_slots - len(inv.items)
        max_by_inv  = existing_cap + free_slots * preview.max_stack
        if max_by_gold == 0:
            LOG.add("Ouro insuficiente!", (220, 80, 80))
            return
        if max_by_inv == 0:
            LOG.add("Inventario cheio!", (255, 160, 0))
            return
        max_qty     = max(1, min(max_by_gold, max_by_inv, preview.max_stack * 10))
        self._qty_modal = {
            "entry":    entry,
            "shop_id":  shop_id,
            "preview":  preview,
            "max_qty":  max_qty,
            "qty":      1,
            "text":     "1",
            "dragging": False,
        }

    def _close_qty_modal(self) -> None:
        self._qty_modal = None

    def _sell(self, item_idx: int) -> None:
        """Envia SELL_REQUEST. Remove item imediatamente (feedback visual), mas
        NÃO altera gold — gold é atualizado quando SELL_RESULT chegar (servidor
        é autoritativo), mesmo padrão de _buy/_buy_qty."""
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if not inv or not wallet or item_idx >= len(inv.items) or not self._net:
            return
        item = inv.items[item_idx]
        from shared.messages import MsgType as _MTS
        self._net.send(_MTS.SELL_REQUEST, {
            "item_name":    item.name,
            "item_id":      getattr(item, "item_id", ""),
            "item_value":   getattr(item, "value", 0),
            "stack_sold":   1,
            "current_gold": wallet.gold,
        })
        item.stack -= 1
        if item.stack <= 0:
            inv.items.pop(item_idx)
        if self._bag_scroll > 0 and self._bag_scroll >= len(inv.items):
            self._bag_scroll = max(0, len(inv.items) - 1)

    def _close(self) -> None:
        self.open_merchant_id     = -1
        self._pending_merchant_id = -1
        self._shop_scroll         = 0
        self._bag_scroll          = 0

    # ------------------------------------------------------------------
    # Helpers de posição/tile
    # ------------------------------------------------------------------

    def _player_tile(self):
        tm = self.world.get_component(self.player_entity, TileMovement)
        return (tm.current_tile_x, tm.current_tile_y) if tm else None

    def _merchant_tile(self, eid: int):
        pos = self.world.get_component(eid, Position)
        return (int(pos.x / TILE_SIZE), int(pos.y / TILE_SIZE)) if pos else None

    @staticmethod
    def _cheby(t1, t2) -> int:
        return chebyshev(t1[0], t1[1], t2[0], t2[1])

    # ------------------------------------------------------------------
    # Eventos de UI
    # ------------------------------------------------------------------

    def handle_events(self, events: list) -> None:
        if not self.is_open:
            return
        merch = self.world.get_component(self.open_merchant_id, Merchant)
        if not merch:
            self._close()
            return

        if merch.shop_id == "instance_shop":
            _char_ev  = self.world.get_component(self.player_entity, CharacterStats)
            stock = INSTANCE_SHOP_STOCK_BY_CLASS.get(_char_ev.class_id if _char_ev else None, [])
        else:
            stock = SHOPS.get(merch.shop_id, {}).get("stock", [])
        inv    = self.world.get_component(self.player_entity, Inventory)
        x0, y0 = self._panel_origin()
        gap     = self._u(self.GAP)
        left_w  = self._u(self.LEFT_W)
        right_w = self._u(self.RIGHT_W)
        row_h   = self._u(self.ROW_H)
        mid_x   = x0 + gap + left_w
        body_y  = y0 + self._u(self.BODY_Y_OFFSET)

        for event in events:
            # ── Modal de quantidade aberto → processa antes de tudo ────────
            if self._qty_modal is not None:
                self._handle_qty_modal_event(event)
                continue  # consome o evento; não deixa cair na lógica da loja

            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._close()
                return

            if event.type == pygame.MOUSEWHEEL:
                if mx < mid_x:
                    self._shop_scroll = max(0, self._shop_scroll - event.y)
                else:
                    self._bag_scroll  = max(0, self._bag_scroll  - event.y)
                continue

            if event.type != pygame.MOUSEBUTTONDOWN:
                continue
            mx, my = event.pos

            # Botão fechar
            close_r = pygame.Rect(x0 + self._u(self.PANEL_W) - self._u(40), y0 + self._u(6), self._u(34), self._u(34))
            if event.button == 1 and close_r.collidepoint(mx, my):
                self._close()
                return

            mods = pygame.key.get_mods()
            shift = bool(mods & pygame.KMOD_SHIFT)

            # Painel esquerdo: comprar
            if event.button == 3 and mx < mid_x and self._open_cooldown <= 0:
                for i, entry in enumerate(stock):
                    vis_i = i - self._shop_scroll
                    if 0 <= vis_i < self.MAX_ROWS:
                        r = pygame.Rect(x0 + gap,
                                        body_y + vis_i * row_h,
                                        left_w - self._u(4), row_h - self._u(2))
                        if r.collidepoint(mx, my):
                            preview = entry["factory"]()
                            if shift and getattr(preview, "max_stack", 1) > 1:
                                self._open_qty_modal(entry, shop_id=merch.shop_id)
                            else:
                                self._buy(entry, shop_id=merch.shop_id)
                            return

            # Painel direito: vender (clique direito)
            if event.button == 3 and mx >= mid_x and inv and self._open_cooldown <= 0:
                for i, item in enumerate(inv.items):
                    vis_i = i - self._bag_scroll
                    if 0 <= vis_i < self.MAX_ROWS:
                        r = pygame.Rect(mid_x + gap,
                                        body_y + vis_i * row_h,
                                        right_w - self._u(4), row_h - self._u(2))
                        if r.collidepoint(mx, my):
                            self._sell(i)
                            return

    def _handle_qty_modal_event(self, event) -> None:
        """Processa eventos enquanto o modal de quantidade está aberto."""
        m = self._qty_modal

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._close_qty_modal()
            elif event.key == pygame.K_RETURN or event.key == pygame.K_KP_ENTER:
                self._buy_qty(m["entry"], m["qty"], shop_id=m.get("shop_id", ""))
                self._close_qty_modal()
            elif event.key == pygame.K_BACKSPACE:
                m["text"] = m["text"][:-1] or "0"
                try:
                    m["qty"] = max(1, min(int(m["text"]), m["max_qty"]))
                except ValueError:
                    m["qty"] = 1
            elif event.unicode.isdigit():
                new_text = (m["text"] if m["text"] != "0" else "") + event.unicode
                if len(new_text) <= 6:
                    m["text"] = new_text
                    try:
                        m["qty"] = max(1, min(int(new_text), m["max_qty"]))
                    except ValueError:
                        pass
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            self._set_panel_scale(UI.SHOP_QTY_MODAL_W, UI.SHOP_QTY_MODAL_H)
            SW, SH  = self.hud_surf.get_size()
            mw, mh  = self._u(UI.SHOP_QTY_MODAL_W), self._u(UI.SHOP_QTY_MODAL_H)
            mx0     = (SW - mw) // 2 + UI.SHOP_QTY_MODAL_OFFSET_X
            my0     = (SH - mh) // 2 + UI.SHOP_QTY_MODAL_OFFSET_Y

            # Slider
            sl_x  = mx0 + self._u(20)
            sl_y  = my0 + self._u(130)
            sl_w  = mw - self._u(40)
            sl_r  = pygame.Rect(sl_x, sl_y - self._u(10), sl_w, self._u(20))
            if sl_r.collidepoint(mx, my):
                ratio      = max(0.0, min(1.0, (mx - sl_x) / sl_w))
                m["qty"]   = max(1, round(ratio * m["max_qty"]))
                m["text"]  = str(m["qty"])
                m["dragging"] = True
                return

            # Botão Cancelar
            btn_cancel = pygame.Rect(mx0 + self._u(20),      my0 + mh - self._u(54), self._u(190), self._u(38))
            btn_ok     = pygame.Rect(mx0 + mw - self._u(210), my0 + mh - self._u(54), self._u(190), self._u(38))
            if btn_cancel.collidepoint(mx, my):
                self._close_qty_modal()
                return
            if btn_ok.collidepoint(mx, my):
                self._buy_qty(m["entry"], m["qty"], shop_id=m.get("shop_id", ""))
                self._close_qty_modal()
                return

            # Clique fora fecha
            modal_r = pygame.Rect(mx0, my0, mw, mh)
            if not modal_r.collidepoint(mx, my):
                self._close_qty_modal()

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            m["dragging"] = False

        elif event.type == pygame.MOUSEMOTION and m.get("dragging"):
            self._set_panel_scale(UI.SHOP_QTY_MODAL_W, UI.SHOP_QTY_MODAL_H)
            SW, SH  = self.hud_surf.get_size()
            mw      = self._u(UI.SHOP_QTY_MODAL_W)
            mx0     = (SW - mw) // 2 + UI.SHOP_QTY_MODAL_OFFSET_X
            sl_x, sl_w = mx0 + self._u(20), mw - self._u(40)
            mx_now  = event.pos[0]
            ratio   = max(0.0, min(1.0, (mx_now - sl_x) / sl_w))
            m["qty"]  = max(1, round(ratio * m["max_qty"]))
            m["text"] = str(m["qty"])

    # ------------------------------------------------------------------
    # Render — NPC no mundo
    # ------------------------------------------------------------------

    def render_world(self, cam_x: float = 0, cam_y: float = 0) -> None:
        pass  # indicador "LOJA" removido — NPC segue o padrão de tooltip

    # ------------------------------------------------------------------
    # Render — painel de loja
    # ------------------------------------------------------------------

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        self.pending_tooltip = None
        if not self.is_open:
            return

        merch = self.world.get_component(self.open_merchant_id, Merchant)
        if not merch:
            self._close()
            return

        shop  = SHOPS.get(merch.shop_id, {})
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        bag    = inv.items if inv else []
        _char_shop  = self.world.get_component(self.player_entity, CharacterStats)
        _viewer_cls = _char_shop.class_id if _char_shop else None
        # Loja de instância: catálogo inteiro filtrado por tema de classe
        # (25/08/2026) — só cosmético aqui, servidor valida de verdade em
        # process_shop_buy (instance_shop_class_allows, mesma função).
        if merch.shop_id == "instance_shop":
            stock = INSTANCE_SHOP_STOCK_BY_CLASS.get(_viewer_cls, [])
        else:
            stock = shop.get("stock", [])

        SW, SH  = self.hud_surf.get_size()
        x0, y0  = self._panel_origin()
        W, H    = self._u(self.PANEL_W), self._u(self.PANEL_H)
        gap     = self._u(self.GAP)
        left_w  = self._u(self.LEFT_W)
        right_w = self._u(self.RIGHT_W)
        row_h   = self._u(self.ROW_H)
        icon_s  = self._u(self.ICON_S)
        mid_x   = x0 + gap + left_w
        mx, my  = pygame.mouse.get_pos()
        _modal_open = self._qty_modal is not None

        # Overlay escuro
        self.hud_surf.blit(fill_surf((SW, SH), (0, 0, 0, 160)), (0, 0))

        # Fundo do painel
        self.hud_surf.blit(fill_surf((W, H), (15, 10, 5, 235)), (x0, y0))
        pygame.draw.rect(self.hud_surf, (140, 100, 60), (x0, y0, W, H), 2, border_radius=4)

        # --- Header ---
        title = self._font_lg.render(f"  {shop.get('name', 'Comerciante')}", False, (255, 220, 120))
        self.hud_surf.blit(title, (x0 + self._u(8), y0 + self._u(8)))

        close_r   = pygame.Rect(x0 + W - self._u(40), y0 + self._u(6), self._u(34), self._u(34))
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.hud_surf, (180, 60, 60) if close_hov else (100, 35, 35), close_r, border_radius=3)
        xs = self._font_md.render("X", False, (255, 255, 255))
        self.hud_surf.blit(xs, (close_r.centerx - xs.get_width() // 2,
                              close_r.centery - xs.get_height() // 2))

        LINE1 = y0 + self._u(50)   # linha após o título
        UNDO_Y = y0 + self._u(54)  # barra de desfazer
        LINE2 = y0 + self._u(92)   # linha após desfazer
        COL_Y = y0 + self._u(96)   # cabeçalhos das colunas
        body_y = y0 + self._u(self.BODY_Y_OFFSET)

        pygame.draw.line(self.hud_surf, (90, 70, 40), (x0 + self._u(4), LINE1), (x0 + W - self._u(4), LINE1))

        # --- Barra de desfazer ---
        undo_r   = pygame.Rect(x0 + gap, UNDO_Y, self._u(145), self._u(32))
        has_hist = bool(self.transaction_history)
        undo_hov = undo_r.collidepoint(mx, my) and has_hist
        undo_bg  = (55, 80, 55) if undo_hov else ((38, 55, 38) if has_hist else (28, 28, 28))
        undo_col = (150, 220, 150) if has_hist else (70, 70, 70)
        pygame.draw.rect(self.hud_surf, undo_bg,  undo_r, border_radius=3)
        pygame.draw.rect(self.hud_surf, undo_col, undo_r, 1, border_radius=3)
        self.hud_surf.blit(self._font_sm.render("↩ Desfazer", False, undo_col),
                         (undo_r.x + self._u(8), undo_r.y + self._u(7)))

        if self.transaction_history:
            tx  = self.transaction_history[-1]
            if tx["type"] == "buy":
                desc = f"Ultima: comprou {tx['item'].name} por {tx['price']}g"
            else:
                desc = f"Ultima: vendeu {tx['item'].name} por {tx['sell_value']}g"
            self.hud_surf.blit(self._font_sm.render(desc, False, (150, 150, 150)),
                             (x0 + gap + self._u(155), UNDO_Y + self._u(7)))

        pygame.draw.line(self.hud_surf, (90, 70, 40), (x0 + self._u(4), LINE2), (x0 + W - self._u(4), LINE2))

        # Divisor vertical
        pygame.draw.line(self.hud_surf, (90, 70, 40), (mid_x, LINE1), (mid_x, y0 + H - self._u(36)))

        # --- Cabeçalhos das colunas ---
        hdr_col = (160, 130, 80)
        hint    = (90, 80, 60)
        self.hud_surf.blit(self._font_md.render(f"LOJA  ({len(stock)} itens)", False, hdr_col),
                         (x0 + gap + self._u(4), COL_Y))
        self.hud_surf.blit(self._font_sm.render("clique dir. p/ comprar  |  Shift+dir. = qtd.", False, hint),
                         (x0 + gap + self._u(4), COL_Y + self._u(26)))
        self.hud_surf.blit(self._font_md.render(f"MOCHILA  ({len(bag)}/{inv.max_slots if inv else 0})", False, hdr_col),
                         (mid_x + gap + self._u(4), COL_Y))
        self.hud_surf.blit(self._font_sm.render("clique dir. p/ vender", False, hint),
                         (mid_x + gap + self._u(4), COL_Y + self._u(26)))

        pygame.draw.line(self.hud_surf, (70, 55, 30), (x0 + self._u(4), body_y - self._u(2)), (x0 + W - self._u(4), body_y - self._u(2)))

        # --- Painel esquerdo: itens da loja ---
        max_shop = max(0, len(stock) - self.MAX_ROWS)
        self._shop_scroll = min(self._shop_scroll, max_shop)

        for i, entry in enumerate(stock):
            vis_i = i - self._shop_scroll
            if not (0 <= vis_i < self.MAX_ROWS):
                continue
            row_y = body_y + vis_i * row_h
            r     = pygame.Rect(x0 + gap, row_y, left_w - self._u(4), row_h - self._u(2))

            # Preview do item (instância temporária apenas para display)
            preview    = entry["factory"]()
            can_afford = wallet and wallet.gold >= entry["price"]
            inv_full   = inv and len(inv.items) >= inv.max_slots

            hov  = r.collidepoint(mx, my) and not _modal_open
            if not can_afford or inv_full:
                bg_c   = (40, 18, 18) if hov else (22, 10, 10)
                bord_c = (110, 45, 45) if hov else (48, 22, 22)
            else:
                bg_c   = (50, 40, 20) if hov else (28, 20, 10)
                bord_c = (180, 140, 60) if hov else (60, 45, 25)

            pygame.draw.rect(self.hud_surf, bg_c,   r, border_radius=3)
            pygame.draw.rect(self.hud_surf, bord_c, r, 1, border_radius=3)

            rar_col = self._RARITY_COLORS.get(preview.rarity, (100, 100, 100))
            ic_r    = pygame.Rect(r.x + self._u(4), r.y + (row_h - self._u(2) - icon_s) // 2,
                                  icon_s, icon_s)
            pygame.draw.rect(self.hud_surf, (38, 30, 14), ic_r, border_radius=2)
            icon_surf = ICONS.get(ICONS.item_key(preview), icon_s)
            if icon_surf:
                self.hud_surf.blit(icon_surf, ic_r)
            else:
                pygame.draw.rect(self.hud_surf, rar_col, ic_r, 1, border_radius=2)
                pygame.draw.circle(self.hud_surf, rar_col, (ic_r.right - self._u(4), ic_r.bottom - self._u(4)), self._u(3))

            name_col = rar_col if (can_afford and not inv_full) else (90, 70, 70)
            self.hud_surf.blit(self._font_sm.render(preview.name, False, name_col),
                             (ic_r.right + self._u(6), r.y + self._u(6)))
            self.hud_surf.blit(self._font_sm.render(preview.item_type, False, (95, 85, 65)),
                             (ic_r.right + self._u(6), r.y + self._u(24)))

            price_col = (255, 215, 0) if (can_afford and not inv_full) else (130, 70, 70)
            ps = self._font_sm.render(f"{entry['price']}g", False, price_col)
            self.hud_surf.blit(ps, (r.right - ps.get_width() - self._u(8), r.y + self._u(14)))

            if hov:
                lines = item_tooltip_lines(preview, _viewer_cls)
                if not can_afford:
                    lines.append(("Ouro insuficiente!", (220, 80, 80)))
                elif inv_full:
                    lines.append(("Mochila cheia!", (220, 150, 50)))
                lines.append((f"Preco: {entry['price']}g | Venda estimada: {self._sell_price(preview)}g",
                              (120, 120, 120)))
                # 7-tuple: title_color + Shift+hover comparison with equipped item
                equip_c = self.world.get_component(self.player_entity, Equipment)
                eq_item = equip_c.slots.get(preview.slot) if equip_c and preview.slot else None
                self.pending_tooltip = (mx, my, preview.name, lines, rar_col, preview, eq_item)

        # Scrollbar loja
        if len(stock) > self.MAX_ROWS:
            sb_h    = self.MAX_ROWS * row_h
            sb_x    = x0 + gap + left_w - self._u(8)
            th      = max(20, sb_h * self.MAX_ROWS // len(stock))
            ty      = body_y + (sb_h - th) * self._shop_scroll // max(1, max_shop)
            pygame.draw.rect(self.hud_surf, (45, 35, 20), (sb_x, body_y, self._u(5), sb_h), border_radius=2)
            pygame.draw.rect(self.hud_surf, (140, 110, 60), (sb_x, ty, self._u(5), th), border_radius=2)

        # --- Painel direito: mochila ---
        max_bag = max(0, len(bag) - self.MAX_ROWS)
        self._bag_scroll = min(self._bag_scroll, max_bag)

        for i, item in enumerate(bag):
            vis_i = i - self._bag_scroll
            if not (0 <= vis_i < self.MAX_ROWS):
                continue
            row_y = body_y + vis_i * row_h
            r     = pygame.Rect(mid_x + gap, row_y, right_w - self._u(4), row_h - self._u(2))

            hov    = r.collidepoint(mx, my) and not _modal_open
            bg_c   = (50, 40, 20) if hov else (28, 20, 10)
            bord_c = (180, 140, 60) if hov else (60, 45, 25)
            pygame.draw.rect(self.hud_surf, bg_c,   r, border_radius=3)
            pygame.draw.rect(self.hud_surf, bord_c, r, 1, border_radius=3)

            rar_col = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
            ic_r    = pygame.Rect(r.x + self._u(4), r.y + (row_h - self._u(2) - icon_s) // 2,
                                  icon_s, icon_s)
            pygame.draw.rect(self.hud_surf, (38, 30, 14), ic_r, border_radius=2)
            icon_surf = ICONS.get(ICONS.item_key(item), icon_s)
            if icon_surf:
                self.hud_surf.blit(icon_surf, ic_r)
            else:
                pygame.draw.rect(self.hud_surf, rar_col, ic_r, 1, border_radius=2)
                pygame.draw.circle(self.hud_surf, rar_col, (ic_r.right - self._u(4), ic_r.bottom - self._u(4)), self._u(3))
            from ui.ui_helpers import draw_stack_count as _dsc
            _dsc(self.hud_surf, item, ic_r, self._font_sm)

            stack = getattr(item, "stack", 1)
            name_label = f"{item.name}" if stack <= 1 else f"{item.name} x{stack}"
            self.hud_surf.blit(self._font_sm.render(name_label, False, rar_col),
                             (ic_r.right + self._u(6), r.y + self._u(6)))
            slot_label = item.slot if item.slot else item.item_type
            self.hud_surf.blit(self._font_sm.render(slot_label, False, (95, 85, 65)),
                             (ic_r.right + self._u(6), r.y + self._u(24)))

            sp     = self._sell_price(item)
            sp_s   = self._font_sm.render(f"+{sp}g", False, (120, 200, 100))
            self.hud_surf.blit(sp_s, (r.right - sp_s.get_width() - self._u(8), r.y + self._u(14)))

            if hov:
                lines = item_tooltip_lines(item, _viewer_cls)
                if stack > 1:
                    lines.append((f"Quantidade: {stack}", (180, 180, 180)))
                lines.append((f"Venda: {sp}g | Valor base: {item.value}g", (120, 120, 120)))
                equip_c = self.world.get_component(self.player_entity, Equipment)
                eq_item = equip_c.slots.get(item.slot) if equip_c and item.slot else None
                self.pending_tooltip = (mx, my, item.name, lines, rar_col, item, eq_item)

        # Scrollbar mochila
        if len(bag) > self.MAX_ROWS:
            sb_h = self.MAX_ROWS * row_h
            sb_x = mid_x + gap + right_w - self._u(8)
            th   = max(20, sb_h * self.MAX_ROWS // len(bag))
            ty   = body_y + (sb_h - th) * self._bag_scroll // max(1, max_bag)
            pygame.draw.rect(self.hud_surf, (45, 35, 20), (sb_x, body_y, self._u(5), sb_h), border_radius=2)
            pygame.draw.rect(self.hud_surf, (140, 110, 60), (sb_x, ty, self._u(5), th), border_radius=2)

        # --- Footer: ouro do jogador ---
        foot_y = y0 + H - self._u(self.FOOTER_H)
        pygame.draw.line(self.hud_surf, (90, 70, 40), (x0 + self._u(4), foot_y), (x0 + W - self._u(4), foot_y))
        if wallet:
            gold_s = self._font_md.render(f"Seu ouro: {wallet.gold}g", False, (255, 215, 0))
            self.hud_surf.blit(gold_s, (x0 + W // 2 - gold_s.get_width() // 2, foot_y + self._u(10)))

        # --- Modal de quantidade ---
        if self._qty_modal is not None:
            self.pending_tooltip = None  # modal suprime tooltip dos itens abaixo
            self._render_qty_modal(wallet)

    def _render_qty_modal(self, wallet) -> None:
        """Renderiza o modal de seleção de quantidade."""
        m       = self._qty_modal
        self._set_panel_scale(UI.SHOP_QTY_MODAL_W, UI.SHOP_QTY_MODAL_H)
        SW, SH  = self.hud_surf.get_size()
        mw, mh  = self._u(UI.SHOP_QTY_MODAL_W), self._u(UI.SHOP_QTY_MODAL_H)
        mx0     = (SW - mw) // 2 + UI.SHOP_QTY_MODAL_OFFSET_X
        my0     = (SH - mh) // 2 + UI.SHOP_QTY_MODAL_OFFSET_Y

        # Overlay semitransparente
        self.hud_surf.blit(fill_surf((SW, SH), (0, 0, 0, 130)), (0, 0))

        # Fundo do modal
        self.hud_surf.blit(fill_surf((mw, mh), (18, 14, 8, 245)), (mx0, my0))
        pygame.draw.rect(self.hud_surf, (180, 140, 70), (mx0, my0, mw, mh), 2, border_radius=6)

        # Título
        title_s = self._font_md.render(m["preview"].name, False, (255, 220, 100))
        self.hud_surf.blit(title_s, (mx0 + mw // 2 - title_s.get_width() // 2, my0 + self._u(12)))

        # Preço
        price    = m["entry"]["price"]
        total    = price * m["qty"]
        gold_avail = wallet.gold if wallet else 0
        price_col  = (255, 215, 0) if total <= gold_avail else (220, 80, 80)
        price_s  = self._font_sm.render(
            f"{price}g por unidade  |  Total: {total}g  (ouro: {gold_avail}g)", False, price_col)
        self.hud_surf.blit(price_s, (mx0 + mw // 2 - price_s.get_width() // 2, my0 + self._u(42)))

        # ── Slider ────────────────────────────────────────────────────────
        sl_x  = mx0 + self._u(20)
        sl_y  = my0 + self._u(130)
        sl_w  = mw - self._u(40)
        ratio = (m["qty"] - 1) / max(1, m["max_qty"] - 1) if m["max_qty"] > 1 else 0.0
        handle_x = sl_x + int(ratio * sl_w)

        pygame.draw.rect(self.hud_surf, (50, 40, 25), (sl_x, sl_y - self._u(3), sl_w, self._u(6)), border_radius=3)
        pygame.draw.rect(self.hud_surf, (160, 120, 50), (sl_x, sl_y - self._u(3), int(ratio * sl_w), self._u(6)), border_radius=3)
        pygame.draw.circle(self.hud_surf, (220, 180, 80), (handle_x, sl_y), self._u(10))
        pygame.draw.circle(self.hud_surf, (255, 220, 120), (handle_x, sl_y), self._u(10), 2)

        # Labels min/max do slider
        self.hud_surf.blit(self._font_sm.render("1", False, (130, 110, 70)),
                           (sl_x, sl_y + self._u(14)))
        max_s = self._font_sm.render(str(m["max_qty"]), False, (130, 110, 70))
        self.hud_surf.blit(max_s, (sl_x + sl_w - max_s.get_width(), sl_y + self._u(14)))

        # ── Campo de texto ────────────────────────────────────────────────
        qty_s = self._font_lg.render(str(m["qty"]), False, (255, 255, 255))
        txt_x = mx0 + mw // 2 - qty_s.get_width() // 2
        self.hud_surf.blit(qty_s, (txt_x, my0 + self._u(76)))
        # Cursor piscante
        if (pygame.time.get_ticks() // 500) % 2 == 0:
            cx = txt_x + qty_s.get_width() + 2
            pygame.draw.line(self.hud_surf, (200, 200, 200),
                             (cx, my0 + self._u(78)), (cx, my0 + self._u(78) + qty_s.get_height() - 4), 2)

        # ── Botões ────────────────────────────────────────────────────────
        btn_cancel = pygame.Rect(mx0 + self._u(20),       my0 + mh - self._u(54), self._u(190), self._u(38))
        btn_ok     = pygame.Rect(mx0 + mw - self._u(210), my0 + mh - self._u(54), self._u(190), self._u(38))
        mmx, mmy   = pygame.mouse.get_pos()

        for btn, label, ok in ((btn_cancel, "Cancelar", False), (btn_ok, f"Comprar {m['qty']}", True)):
            can_buy  = ok and total <= gold_avail
            hov      = btn.collidepoint(mmx, mmy)
            if ok:
                col_bg   = (40, 100, 40) if (can_buy and hov) else ((30, 75, 30) if can_buy else (50, 25, 25))
                col_brd  = (100, 220, 100) if can_buy else (120, 60, 60)
                col_txt  = (150, 255, 150) if can_buy else (180, 100, 100)
            else:
                col_bg  = (70, 40, 30) if hov else (50, 28, 20)
                col_brd = (180, 100, 60)
                col_txt = (220, 160, 100)
            pygame.draw.rect(self.hud_surf, col_bg,  btn, border_radius=4)
            pygame.draw.rect(self.hud_surf, col_brd, btn, 1, border_radius=4)
            lbl_s = self._font_sm.render(label, False, col_txt)
            self.hud_surf.blit(lbl_s, (btn.centerx - lbl_s.get_width() // 2,
                                        btn.centery - lbl_s.get_height() // 2))

        # Dica ESC
        esc_s = self._font_sm.render("ESC cancela  |  ENTER confirma", False, (80, 70, 50))
        self.hud_surf.blit(esc_s, (mx0 + mw // 2 - esc_s.get_width() // 2, my0 + mh - self._u(14)))


