# shop_system.py
"""
ShopSystem — painel de comerciante NPC (clique direito abre; painel
esquerdo compra do mercador, painel direito vende da mochila do
jogador). Extraído de ui/systems.py (18/09/2026, ver
PROBLEMAS_ARQUITETURA.md §185, item #2 do backlog de 14).
"""
from __future__ import annotations
import math
import pygame
# TODO(B4): _font é usado apenas em sistemas visuais (HUD, FloatingText). O servidor
# importa `from systems import EnemyAISystem` e acaba carregando Pygame fonts pelo
# caminho. Funciona com SDL_VIDEODRIVER=dummy, mas é dependência desnecessária.
# Fix futuro: mover sistemas visuais para ClientSystems ou usar lazy import em render().
from ui.fonts import make as _font
from ui import atlas_window as aw
from ui import ui_atlas
from ui.ui_scale_mixin import UIScaleMixin

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
from ui.ui_helpers import item_tooltip_lines
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
    - Esquerda: itens à venda (clique direito = comprar; Shift = quantidade).
    - Direita: mochila do jogador em grade (clique direito = vender).
    - Visual: atlas de UI (ui/atlas_window.py), ações no SOLTAR do mouse.
    - Botão Desfazer: desfaz a última compra/venda enquanto a janela
      estiver aberta (servidor autoritativo — SHOP_UNDO/SHOP_CLOSE).
    """

    _FONT_BASES = {"_font_xs": 15, "_font_sm": 22, "_font_md": 30, "_font_lg": 38}

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
        self._bag_scroll:  int = 0
        # Online: injetado pelo GameEngine. None = modo offline (lógica local)
        self._net = None
        self.pending_tooltip = None
        self._open_cooldown: float = 0.0  # impede compra/venda logo após abrir a loja
        # Modal de quantidade (Shift+clique direito em item stackável)
        self._qty_modal: dict | None = None  # None = fechado
        # Atlas de UI: botão afundado, rolagem da lista à venda, eventos do
        # frame (guardados em handle_events, consumidos em render), área
        # segura (injetada por game.py) e rects de tela clicáveis (testes).
        self._press = aw.PressState()
        self._shop_scroll_st = aw.ScrollState()
        self._frame_events: list = []
        self.safe_area_fn = None
        self._hit_rects: dict = {}
        self._shop_title = ""

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
        self._reset_undo()
        self.open_merchant_id   = merchant_eid
        self._pending_merchant_id = -1
        self._shop_scroll_st    = aw.ScrollState()
        self._bag_scroll        = 0
        self._open_cooldown     = 0.3

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
            self._reset_undo()
            self.open_merchant_id     = eid
            self._pending_merchant_id = -1
            self._shop_scroll_st = aw.ScrollState()
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
            if (event.type == pygame.MOUSEBUTTONDOWN
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
        self._qty_modal = {**aw.quantity_state(max_qty),
                           "entry": entry, "shop_id": shop_id, "preview": preview}

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
            "npc_eid":      self.open_merchant_id,
        })
        item.stack -= 1
        if item.stack <= 0:
            inv.items.pop(item_idx)
        if self._bag_scroll > 0 and self._bag_scroll >= len(inv.items):
            self._bag_scroll = max(0, len(inv.items) - 1)

    # ── Desfazer (21/10/2026): dura enquanto a janela estiver aberta. O
    # servidor guarda e valida as transações (WorldServer.process_shop_undo);
    # aqui só a contagem pra habilitar o botão, espelhada dos resultados.

    def note_transaction(self, kind: str) -> None:
        """BUY_RESULT/SELL_RESULT de sucesso com a janela aberta."""
        if self.is_open:
            self.transaction_history.append(kind)
            del self.transaction_history[:-self.MAX_HISTORY]

    def on_undo_result(self, undo_left: int) -> None:
        self.transaction_history = self.transaction_history[:max(0, undo_left)] if self.is_open else []

    def _undo(self) -> None:
        if not self._net or not self.transaction_history:
            return
        from shared.messages import MsgType as _MTU
        self._net.send(_MTU.SHOP_UNDO, {"npc_eid": self.open_merchant_id})

    def _reset_undo(self) -> None:
        """Janela abriu/fechou: o histórico do Desfazer some (aqui e no servidor)."""
        self.transaction_history = []
        if self._net:
            from shared.messages import MsgType as _MTC
            self._net.send(_MTC.SHOP_CLOSE, {})

    def _close(self) -> None:
        was_open = self.is_open
        self.open_merchant_id     = -1
        self._pending_merchant_id = -1
        self._shop_scroll_st      = aw.ScrollState()
        self._bag_scroll          = 0
        self._qty_modal           = None
        if was_open:
            self._reset_undo()

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
    # Atlas de UI (21/10/2026): botões/slots agem no SOLTAR dentro de
    # render() (ui/atlas_window.py); aqui só ESC e o teclado do modal de
    # quantidade, e os eventos do frame ficam guardados pro render.

    def handle_events(self, events: list) -> None:
        if not self.is_open:
            return
        if not self.world.get_component(self.open_merchant_id, Merchant):
            self._close()
            return
        for event in events:
            if self._qty_modal is not None:
                res = aw.quantity_key(self._qty_modal, event)
                if res == "ok":
                    self._confirm_qty_modal()
                elif res == "cancel":
                    self._close_qty_modal()
                continue
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._close()
                return
        self._frame_events = list(events)

    def _confirm_qty_modal(self) -> None:
        m = self._qty_modal
        self._buy_qty(m["entry"], m["qty"], shop_id=m.get("shop_id", ""))
        self._close_qty_modal()

    # ------------------------------------------------------------------
    # Render — NPC no mundo
    # ------------------------------------------------------------------

    def render_world(self, cam_x: float = 0, cam_y: float = 0) -> None:
        pass  # indicador "LOJA" removido — NPC segue o padrão de tooltip

    # ------------------------------------------------------------------
    # Render — painel de loja (atlas de UI)
    # ------------------------------------------------------------------
    # Janela escura com barra de título (nome da loja) e X. Esquerda:
    # mochila em grade (mesma do inventário). Divisória. Direita: lista à
    # venda (slot + nome + preço, rolável) com a barra de rolagem encostada
    # na borda direita da janela (padrão de todo modal). Embaixo: Desfazer
    # e saldo. Clique DIREITO compra/vende (Shift+direito = quantidade);
    # age no soltar. Medidas em px RAW (slot de ícone 32x32 — aw.SLOT).

    _LIST_ROWS  = 4                 # linhas visíveis da lista à venda
    _ROW_H      = aw.SLOT + 2       # px RAW por linha
    _BAG_COLS   = 5
    _PAD        = 6                 # folga da borda da janela
    _TOP        = 32                # topo da mochila/lista (abaixo do cabeçalho)

    def _area(self) -> pygame.Rect:
        if self.safe_area_fn is not None:
            return self.safe_area_fn()
        SW, SH = self.hud_surf.get_size()
        return aw.safe_area(SW, SH, SH)

    def _stock(self, merch) -> list:
        if merch.shop_id == "instance_shop":
            char = self.world.get_component(self.player_entity, CharacterStats)
            return INSTANCE_SHOP_STOCK_BY_CLASS.get(char.class_id if char else None, [])
        return SHOPS.get(merch.shop_id, {}).get("stock", [])

    def _layout(self, stock_previews, stock, inv) -> dict:
        T, S = aw.T, aw.S
        fsm = self._font_sm
        name_w = max([fsm.size(p.name)[0] for p in stock_previews] + [fsm.size("À venda (99)")[0]]) / S
        price_w = max([fsm.size(f"{e['price']}g")[0] for e in stock] + [1]) / S
        bag_rows = math.ceil((inv.max_slots if inv else 20) / self._BAG_COLS)
        bag_x0 = self._PAD + 2
        bag_w = aw.grid_px(self._BAG_COLS)
        list_x0 = bag_x0 + bag_w + 16
        list_w = math.ceil(aw.SLOT + 6 + name_w + 10 + price_w + 4)      # px RAW
        # coluna da barra de rolagem = última coluna da janela (encostada
        # na borda direita); a lista termina logo antes dela.
        tw = max(math.ceil((list_x0 + list_w) / T) + 1,
                 aw.tiles_for(self._font_md, [self._shop_title], 1) + 3)
        list_w = (tw - 1) * T - list_x0
        body_h = max(self._LIST_ROWS * self._ROW_H, aw.grid_px(bag_rows))
        foot_y = self._TOP + body_h + 6                                 # divisória do rodapé
        th = math.ceil((foot_y + T + 8) / T)
        return {"tw": tw, "th": th, "bag_x0": bag_x0, "bag_w": bag_w, "bag_rows": bag_rows,
                "list_x0": list_x0, "list_w": list_w, "body_h": body_h, "foot_y": foot_y,
                "div_x": bag_x0 + bag_w + 8}

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        events, self._frame_events = self._frame_events, []
        self.pending_tooltip = None
        self._hit_rects = {}
        if not self.is_open:
            return
        merch = self.world.get_component(self.open_merchant_id, Merchant)
        if not merch:
            self._close()
            return
        T, S = aw.T, aw.S
        shop   = SHOPS.get(merch.shop_id, {})
        inv    = self.world.get_component(self.player_entity, Inventory)
        wallet = self.world.get_component(self.player_entity, Wallet)
        equip  = self.world.get_component(self.player_entity, Equipment)
        bag    = inv.items if inv else []
        char   = self.world.get_component(self.player_entity, CharacterStats)
        viewer_cls = char.class_id if char else None
        stock = self._stock(merch)
        previews = [e["factory"]() for e in stock]
        self._shop_title = shop.get("name", "Comerciante")
        modal_open = self._qty_modal is not None
        lay = self._layout(previews, stock, inv)
        tw, th = lay["tw"], lay["th"]
        f = aw.Frame(self.hud_surf, [] if modal_open else events, tw, th, area=self._area(),
                     press=None if modal_open else self._press, font=self._font_md,
                     raw=ui_atlas.titled_dark_window_raw(tw, th))
        mx, my = pygame.mouse.get_pos()
        ip = aw.slot_icon_px()
        can_act = self._open_cooldown <= 0 and not modal_open
        shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)

        f.text(self._shop_title, (tw - 1) * T / 2, aw.TITLE_BAR_Y, (240, 228, 200))
        if f.close_button("shop:close", tw - 1):
            self._close()
            return
        self._hit("shop:close", f, (tw - 1) * T, 0, tw * T, T)

        hdr = (190, 170, 120)
        top, lx0, list_w = self._TOP, lay["list_x0"], lay["list_w"]
        f.text(f"Mochila {len(bag)}/{inv.max_slots if inv else 0}", lay["bag_x0"], 24,
               hdr, self._font_sm, anchor="midleft")
        f.text(f"À venda ({len(stock)})", lx0, 24, hdr, self._font_sm, anchor="midleft")
        f.divider_v(lay["div_x"], T, lay["foot_y"])
        f.divider_h(lay["foot_y"], T, (tw - 1) * T)

        # ── Lista à venda (rolável; barra encostada na borda direita) ─────
        view_y0, view_h = top, self._LIST_ROWS * self._ROW_H
        content_h = max(1, len(stock) * self._ROW_H)
        off = f.scrollbar(self._shop_scroll_st, tw - 1, view_y0 // T, (view_y0 + view_h) // T - 1,
                          max(0, content_h - view_h), step=self._ROW_H)
        in_view = view_y0 <= f.ry < view_y0 + view_h and lx0 <= f.rx < lx0 + list_w
        fc = f.child(pygame.Surface((list_w, content_h), pygame.SRCALPHA),
                     f.ry - view_y0 + off if in_view else -10_000)
        fc.rx = f.rx - lx0
        inv_full = inv is not None and len(inv.items) >= inv.max_slots
        mid = aw.SLOT / 2
        for i, (entry, prev) in enumerate(zip(stock, previews)):
            y = i * self._ROW_H + 1
            ok = (wallet is not None and wallet.gold >= entry["price"]) and not inv_full
            rar = self._RARITY_COLORS.get(prev.rarity, (200, 200, 200))
            hov, _ = fc.slot(f"shop:buy:{i}", 0, y, ICONS.get(ICONS.item_key(prev), ip),
                             dark=True, dim=not ok)
            row_hov = fc.hover(0, y - 1, list_w, y - 1 + self._ROW_H)
            fc.text(prev.name, aw.SLOT + 6, y + mid, rar if ok else (110, 90, 90),
                    self._font_sm, anchor="midleft")
            fc.text(f"{entry['price']}g", list_w - 4, y + mid,
                    (255, 215, 0) if ok else (130, 70, 70), self._font_sm, anchor="midright")
            ry = view_y0 + y - off
            if view_y0 <= ry and ry + aw.SLOT <= view_y0 + view_h:
                self._hit(f"shop:buy:{i}", f, lx0, ry - 1, lx0 + list_w, ry - 1 + self._ROW_H)
            if row_hov or hov:
                lines = item_tooltip_lines(prev, viewer_cls)
                if wallet is None or wallet.gold < entry["price"]:
                    lines.append(("Ouro insuficiente!", (220, 80, 80)))
                elif inv_full:
                    lines.append(("Mochila cheia!", (220, 150, 50)))
                lines.append((f"Preço: {entry['price']}g | Venda estimada: {self._sell_price(prev)}g",
                              (120, 120, 120)))
                lines.append(("Clique dir. p/ comprar | Shift+dir. = quantidade", (140, 140, 140)))
                eq_item = equip.slots.get(prev.slot) if equip and prev.slot else None
                self.pending_tooltip = (mx, my, prev.name, lines, rar, prev, eq_item)
                if can_act and fc.rreleased:
                    if shift and getattr(prev, "max_stack", 1) > 1:
                        self._open_qty_modal(entry, shop_id=merch.shop_id)
                    else:
                        self._buy(entry, shop_id=merch.shop_id)

        # ── Mochila (clique direito vende) ────────────────────────────────
        cells = []
        for item in bag:
            cells.append(None if item is None else
                         (ICONS.get(ICONS.item_key(item), ip),
                          item.stack if getattr(item, "max_stack", 1) > 1 else 0))
        bx0 = lay["bag_x0"]
        hov_i, _ = f.grid("shop:bag", bx0, top, self._BAG_COLS, lay["bag_rows"], cells,
                          count_font=self._font_xs)
        for i in range(len(bag)):
            c, r = i % self._BAG_COLS, i // self._BAG_COLS
            x, y = bx0 + c * aw.SLOT, top + r * aw.SLOT
            self._hit(f"shop:sell:{i}", f, x, y, x + aw.SLOT, y + aw.SLOT)
        item = bag[hov_i] if 0 <= hov_i < len(bag) else None
        if item is not None:
            sp = self._sell_price(item)
            rar = self._RARITY_COLORS.get(item.rarity, (200, 200, 200))
            lines = item_tooltip_lines(item, viewer_cls)
            if getattr(item, "stack", 1) > 1:
                lines.append((f"Quantidade: {item.stack}", (180, 180, 180)))
            lines.append((f"Venda: {sp}g | Valor base: {item.value}g", (120, 120, 120)))
            lines.append(("Clique dir. p/ vender", (140, 140, 140)))
            eq_item = equip.slots.get(item.slot) if equip and item.slot else None
            self.pending_tooltip = (mx, my, item.name, lines, rar, item, eq_item)
            if can_act and f.rreleased:
                self._sell(hov_i)

        # ── Rodapé: Desfazer (última compra/venda desta janela) + saldo ───
        bw = aw.tiles_for(self._font_sm, ["Desfazer"], 3)
        foot_ty = (lay["foot_y"] + 6) / T
        if f.button("shop:undo", 0.5, foot_ty, bw, "Desfazer", tall=False,
                    enabled=bool(self.transaction_history) and can_act):
            self._undo()
        ay0, ay1 = ui_atlas.button_art_y(False)
        self._hit("shop:undo", f, 0.5 * T, foot_ty * T + ay0, (0.5 + bw) * T, foot_ty * T + ay1)
        if wallet:
            f.text(f"Saldo: {wallet.gold}g", (tw - 1) * T - 4, foot_ty * T + T / 2,
                   (255, 215, 0), self._font_md, anchor="midright")

        f.end()
        content = ui_atlas.scale_up(fc.raw)
        aw.draw_texts(content, fc.texts, self._font_sm)
        aw.draw_overlays(content, fc.overlays)
        view = content.subsurface((0, off * S, content.get_width(),
                                   min(view_h, content_h - off) * S))
        self.hud_surf.blit(view, (f.px + lx0 * S, f.py + view_y0 * S))
        self._panel_rect = pygame.Rect(f.px, f.py, f.pw, f.ph)

        if self._qty_modal is not None:
            self.pending_tooltip = None   # modal suprime tooltip dos itens abaixo
            self._render_qty_modal(wallet, events)

    def _hit(self, hit_id: str, f, x0, y0, x1, y1) -> None:
        """Rect de TELA de um elemento clicável — só pros testes."""
        self._hit_rects[hit_id] = pygame.Rect(f.px + round(x0 * aw.S), f.py + round(y0 * aw.S),
                                              round((x1 - x0) * aw.S), round((y1 - y0) * aw.S))

    def _render_qty_modal(self, wallet, events) -> None:
        m = self._qty_modal
        price = m["entry"]["price"]
        total = price * m["qty"]
        gold = wallet.gold if wallet else 0
        res = aw.quantity_window(
            self.hud_surf, events, m, area=self._area(), press=self._press,
            font=self._font_md, small_font=self._font_sm, title=m["preview"].name,
            info=f"{price}g por unidade  |  Total: {total}g  (ouro: {gold}g)",
            confirm_label=f"Comprar {m['qty']}", confirm_enabled=total <= gold,
            hits=self._hit_rects)
        if res == "ok":
            self._confirm_qty_modal()
        elif res == "cancel":
            self._close_qty_modal()
