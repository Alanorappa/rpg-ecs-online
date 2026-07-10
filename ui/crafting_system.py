# crafting_system.py
"""
BlacksmithSystem — gerencia interação com NPCs ferreiros.

Fluxo:
  1. Clique direito em NPC com componente Blacksmith → action menu
  2. Action menu: Negociar / Reciclar / Forjar / quests disponíveis
  3. Cada opção abre o modal correspondente
"""
from __future__ import annotations
import pygame
from ui.fonts import make as _font
from ui.systems import System
from ui.ui_scale_mixin import UIScaleMixin
from engine.components import (Position, Renderable, TileMovement, PlayerAutoMove,
                        CombatState, Inventory, Wallet, NPC, QuestGiver, Equipment)
from content.crafting_data import (MATERIALS, RECYCLE_TABLE, RECIPES, RECIPE_ITEMS,
                           RARITY_RECYCLE_COST, RARITY_FORGE_COST,
                           get_recycle_materials)
from ui.combat_log import LOG
from engine.tileset import TILE_SIZE
from ui.ui_helpers import item_tooltip_lines, fill_surf
from ui.ui_sizes import UI


# ---------------------------------------------------------------------------
# Constantes visuais
# ---------------------------------------------------------------------------
_RARITY_COL = {
    "common":    (200, 200, 200),
    "uncommon":  (30,  180,  30),
    "rare":      (0,   110, 230),
    "epic":      (160,   0, 220),
    "legendary": (224, 135,  47),
    "mythic":    (221,  68,  68),
}
_COL_BG      = (12,  10,   6)
_COL_PANEL   = (30,  24,  14)
_COL_BORDER  = (110, 82,  36)
_COL_TITLE   = (220, 180,  80)
_COL_WHITE   = (240, 220, 180)
_COL_GREY    = (130, 110,  80)
_COL_GOLD    = (220, 180,  50)
_COL_RED     = (190,  55,  55)
_COL_GREEN   = (50,  150,  50)
_COL_DARK    = (20,  16,   8)

# Layout do modal principal — valores em ui_sizes.py (UI.CRAFTING_*)
_PANEL_W   = UI.CRAFTING_W
_PANEL_H   = UI.CRAFTING_H
_LEFT_W    = UI.CRAFTING_LEFT_W    # largura do painel esquerdo (reciclagem/forja)
_RIGHT_W   = UI.CRAFTING_RIGHT_W   # largura do painel direito (bag)
_DIVIDER   = UI.CRAFTING_DIVIDER   # _LEFT_W + _DIVIDER + _RIGHT_W = _PANEL_W
_PAD       = UI.CRAFTING_PAD

# Bag grid
_SLOT_SZ   = UI.CRAFTING_SLOT_SZ
_SLOT_GAP  = UI.CRAFTING_SLOT_GAP
_BAG_COLS  = UI.CRAFTING_BAG_COLS
_BAG_ROW_H = _SLOT_SZ + _SLOT_GAP

# Slots de item / material
_ITEM_SLOT = UI.CRAFTING_ITEM_SLOT  # slot do item a reciclar / receita
_MAT_SZ    = UI.CRAFTING_MAT_SZ     # slot de material


class BlacksmithSystem(UIScaleMixin, System):
    """Gerencia o menu de ação e os modais de Reciclagem e Forja."""

    # Estado da máquina de estados
    STATE_CLOSED  = "CLOSED"
    STATE_MENU    = "ACTION_MENU"
    STATE_RECYCLE = "RECYCLE"
    STATE_FORGE   = "FORGE"

    _FONT_BASES = {"_font_sm": 20, "_font_md": 26, "_font_lg": 32}

    def __init__(self, world, player_entity, screen,
                 shop_system=None, quest_dialog=None, quest_system=None):
        super().__init__()
        self.world          = world
        self.player_entity  = player_entity
        self.hud_surf         = screen
        self._shop_system   = shop_system
        self._quest_dialog  = quest_dialog
        self._quest_system  = quest_system

        SW, SH = screen.get_size()

        # Máquina de estados
        self._state: str     = self.STATE_CLOSED
        self._bs_eid: int    = -1       # entidade blacksmith ativa
        self._bs_name: str   = ""
        self._pending_bs_id: int = -1   # aguardando aproximação

        # Flags de consumo (lidas pelo game.py antes dos outros sistemas)
        self._right_click_consumed: bool = False

        # --- Estado: Recycle ---
        self._rec_item     = None   # Item no slot de reciclagem
        self._rec_bag_idx  = -1     # índice original na bag
        self._bag_scroll_r = 0

        # --- Estado: Forge ---
        self._frg_selected = None   # recipe_id selecionado na lista
        self._frg_data     = None   # RECIPES[recipe_id]
        self._frg_result   = None   # Item resultado após forjado
        self._frg_complete = False  # Forjou — resultado pode ser lootado
        self._frg_list_scroll = 0   # scroll da lista de receitas
        self._bag_scroll_f = 0

        # --- Contexto de bag (menu "Deletar") ---
        self._ctx_bag_idx  = -1
        self._ctx_mode     = ""     # "recycle" ou "forge"
        self._ctx_pos      = (0, 0)

        # Rects armazenados durante render (para hit-test)
        self._menu_rects:   dict = {}
        self._rec_slot_r:    pygame.Rect | None = None
        self._rec_btn_r:     pygame.Rect | None = None
        self._frg_list_rs:   list = []             # rects da lista de receitas
        self._frg_mat_rs:    list = []             # 5 rects de mat input
        self._frg_result_r:  pygame.Rect | None = None
        self._frg_btn_r:     pygame.Rect | None = None
        self._close_r:      pygame.Rect | None = None
        self._bag_item_rs:  list = []   # grid da bag
        self._ctx_del_r:    pygame.Rect | None = None

        # Tooltip pendente: (mx, my, title, lines, item, eq_item)
        self.pending_tooltip = None

        self._open_cooldown = 0.0

    # ------------------------------------------------------------------
    # Propriedade pública
    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._state != self.STATE_CLOSED

    # ------------------------------------------------------------------
    # Helpers de tile / distância
    # ------------------------------------------------------------------
    def _player_tile(self):
        tm = self.world.get_component(self.player_entity, TileMovement)
        return (tm.current_tile_x, tm.current_tile_y) if tm else None

    def _bs_tile(self, eid: int):
        tm = self.world.get_component(eid, TileMovement)
        if tm:
            return (tm.current_tile_x, tm.current_tile_y)
        pos = self.world.get_component(eid, Position)
        if pos:
            return (int(pos.x // TILE_SIZE), int(pos.y // TILE_SIZE))
        return None

    @staticmethod
    def _cheby(a, b) -> int:
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

    def _walk_to(self, eid: int):
        auto = self.world.get_component(self.player_entity, PlayerAutoMove)
        pt   = self._player_tile()
        bt   = self._bs_tile(eid)
        if auto and pt and bt:
            adj    = [(bt[0]+dx, bt[1]+dy) for dx, dy in ((-1,0),(1,0),(0,-1),(0,1))]
            target = min(adj, key=lambda t: abs(t[0]-pt[0]) + abs(t[1]-pt[1]))
            auto.ground_target     = target
            auto.path              = []
            auto.active            = True
            auto.path_recalc_timer = 0.0

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _panel_origin(self):
        x0, y0 = self._safe_panel_origin(_PANEL_W, _PANEL_H)
        return x0 + UI.CRAFTING_OFFSET_X, y0 + UI.CRAFTING_OFFSET_Y

    # ------------------------------------------------------------------
    # Bag helpers
    # ------------------------------------------------------------------
    def _inv(self):
        return self.world.get_component(self.player_entity, Inventory)

    def _wallet(self):
        return self.world.get_component(self.player_entity, Wallet)

    def _count_mat_in_bag(self, mat_name: str) -> int:
        inv = self._inv()
        if not inv:
            return 0
        return sum(it.stack for it in inv.items if it.name == mat_name)

    def _remove_mat_from_bag(self, mat_name: str, qty: int) -> bool:
        """Remove qty unidades do material. Retorna True se ok."""
        inv = self._inv()
        if not inv:
            return False
        remaining = qty
        to_remove = []
        for i, it in enumerate(inv.items):
            if it.name != mat_name:
                continue
            take = min(remaining, it.stack)
            it.stack -= take
            remaining -= take
            if it.stack <= 0:
                to_remove.append(i)
            if remaining == 0:
                break
        for i in reversed(to_remove):
            inv.items.pop(i)
        return remaining == 0

    def _add_to_bag(self, item) -> bool:
        """Adiciona item à bag. Retorna True se coube."""
        inv = self._inv()
        if not inv:
            return False
        if item.max_stack > 1:
            for ex in inv.items:
                if ex.name == item.name and ex.stack < ex.max_stack:
                    ex.stack += item.stack
                    return True
        if len(inv.items) < inv.max_slots:
            inv.items.append(item)
            return True
        return False

    # ------------------------------------------------------------------
    # update()
    # ------------------------------------------------------------------
    def update(self, events=None, dt: float = 0):
        if events is None:
            events = []

        self._right_click_consumed = False

        if self._open_cooldown > 0:
            self._open_cooldown -= dt

        # Verificar chegada ao NPC pendente
        if self._pending_bs_id != -1:
            pt = self._player_tile()
            bt = self._bs_tile(self._pending_bs_id)
            if pt and bt and self._cheby(pt, bt) <= 1:
                self._open_menu(self._pending_bs_id)
                self._pending_bs_id = -1

        # ESC fecha qualquer estado
        for ev in events:
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                if self._ctx_bag_idx >= 0:
                    self._ctx_bag_idx = -1
                elif self._state != self.STATE_CLOSED:
                    self._close()
                return

        # Detecta clique direito em blacksmith (apenas quando fechado)
        if self._state == self.STATE_CLOSED and self._open_cooldown <= 0:
            from engine.components import Blacksmith
            cam_comp = None
            for eid, pos, rend, _ in self.world.get_entities_with(
                    Position, Renderable, Blacksmith):
                for ev in events:
                    if ev.type != pygame.MOUSEBUTTONDOWN or ev.button != 3:
                        continue
                    # Offset de câmera (derivado do Position da entidade câmera).
                    # Usa world_surf (superfície lógica de zoom), não hud_surf
                    # (tela real) — sem isso o clique calcula a posição mundial
                    # errada quando self._zoom != 1.0 (ver ShopSystem._get_cam()
                    # em systems.py, que já faz certo). Fallback pra hud_surf se
                    # world_surf ainda não foi atribuído (1º frame, antes de
                    # game.py::_assign_world_surf rodar — equivale a zoom=1.0).
                    from engine.components import Camera
                    _wsurf = self.world_surf or self.hud_surf
                    SW, SH = _wsurf.get_size()
                    cx, cy = 0.0, 0.0
                    for _, _, _cam_pos in self.world.get_entities_with(Camera, Position):
                        cx = _cam_pos.x - SW / 2
                        cy = _cam_pos.y - SH / 2
                        break
                    _sc = _wsurf.get_width() / max(1, self.hud_surf.get_width())
                    wx = ev.pos[0] * _sc + cx
                    wy = ev.pos[1] * _sc + cy
                    hw = rend.width  / 2
                    hh = rend.height / 2
                    if abs(wx - pos.x) <= hw and abs(wy - pos.y) <= hh:
                        pt = self._player_tile()
                        bt = self._bs_tile(eid)
                        if pt and bt and self._cheby(pt, bt) <= 1:
                            self._open_menu(eid)
                        else:
                            self._pending_bs_id = eid
                            self._walk_to(eid)
                        self._right_click_consumed = True
                        break
                if self._right_click_consumed:
                    break

    # ------------------------------------------------------------------
    # handle_events() — chamado pelo game.py quando is_open
    # ------------------------------------------------------------------
    def handle_events(self, events):
        for ev in events:
            if ev.type != pygame.MOUSEBUTTONDOWN:
                continue
            mx, my = ev.pos

            # Fecha contexto se clicar fora
            if self._ctx_bag_idx >= 0:
                if ev.button == 1:
                    if self._ctx_del_r and self._ctx_del_r.collidepoint(mx, my):
                        self._do_delete_bag_item(self._ctx_bag_idx)
                    self._ctx_bag_idx = -1
                continue

            # Botão fechar (X)
            if ev.button == 1 and self._close_r and self._close_r.collidepoint(mx, my):
                self._close()
                return

            # --- Action menu ---
            if self._state == self.STATE_MENU and ev.button == 1:
                for key, rect in self._menu_rects.items():
                    if not rect.collidepoint(mx, my):
                        continue
                    if key == "negociar":
                        self._do_negociar()
                    elif key == "reciclar":
                        self._state = self.STATE_RECYCLE
                        self._reset_recycle()
                    elif key == "forjar":
                        self._state = self.STATE_FORGE
                        self._reset_forge()
                    elif key.startswith("quest:"):
                        qid = key[6:]
                        if self._quest_dialog:
                            self._quest_dialog._open_dialog(self._bs_eid)
                        self._state = self.STATE_CLOSED
                    return

            # --- Recycle modal ---
            elif self._state == self.STATE_RECYCLE:
                if ev.button == 1:
                    self._handle_recycle_click(mx, my)
                elif ev.button == 3:
                    self._handle_bag_rclick(mx, my, "recycle")

            # --- Forge modal ---
            elif self._state == self.STATE_FORGE:
                if ev.button == 1:
                    self._handle_forge_click(mx, my)
                elif ev.button == 3:
                    self._handle_bag_rclick(mx, my, "forge")

        # Mouse wheel — scroll bag
        for ev in events:
            if ev.type != pygame.MOUSEWHEEL:
                continue
            if self._state == self.STATE_RECYCLE:
                self._bag_scroll_r = max(0, self._bag_scroll_r - ev.y)
            elif self._state == self.STATE_FORGE:
                mx_w, my_w = pygame.mouse.get_pos()
                x0, y0 = self._panel_origin()
                # Scroll na lista (painel esquerdo) ou na bag (painel direito)
                if mx_w < x0 + self._u(_LEFT_W):
                    self._frg_list_scroll = max(0, self._frg_list_scroll - ev.y)
                else:
                    self._bag_scroll_f = max(0, self._bag_scroll_f - ev.y)

    # ------------------------------------------------------------------
    # Ações internas
    # ------------------------------------------------------------------
    def _open_menu(self, eid: int):
        self._bs_eid  = eid
        npc = self.world.get_component(eid, NPC)
        self._bs_name = npc.name if npc else "Ferreiro"
        self._state   = self.STATE_MENU
        self._open_cooldown = 0.3

    def _close(self):
        self._state         = self.STATE_CLOSED
        self._bs_eid        = -1
        self._pending_bs_id = -1
        self._ctx_bag_idx   = -1
        self._reset_recycle()
        self._reset_forge()
        self._open_cooldown = 0.2

    def _reset_recycle(self):
        self._rec_item    = None
        self._rec_bag_idx = -1

    def _reset_forge(self):
        self._frg_selected = None
        self._frg_data     = None
        self._frg_result   = None
        self._frg_complete = False

    def _do_negociar(self):
        if not self._shop_system:
            return
        self._shop_system.open_for(self._bs_eid)
        self._state = self.STATE_CLOSED

    def _do_recycle(self):
        if self._rec_item is None:
            return
        wallet = self._wallet()
        cost   = RARITY_RECYCLE_COST.get(self._rec_item.rarity, 250)
        if not wallet or wallet.gold < cost:
            LOG.add("Ouro insuficiente para reciclar.", (200, 80, 80))
            return
        mats = get_recycle_materials(self._rec_item)
        if not mats:
            LOG.add("Este item nao pode ser reciclado.", (200, 80, 80))
            return
        wallet.gold -= cost
        item_name = self._rec_item.name
        # Materiais vão direto para a bag
        for mat_id, qty in mats:
            factory = MATERIALS.get(mat_id)
            if not factory:
                continue
            mat_item       = factory()
            mat_item.stack = qty
            if not self._add_to_bag(mat_item):
                LOG.add(f"Bag cheia! {mat_item.name} perdido.", _COL_RED)
        # Limpa o slot e reseta
        self._reset_recycle()
        LOG.add(f"Reciclado: {item_name} (-{cost}g)", _COL_GOLD)

    def _do_forge(self):
        if not self._frg_data or self._frg_complete:
            return
        wallet = self._wallet()
        rarity = self._frg_data.get("result_rarity", "common")
        cost   = RARITY_FORGE_COST.get(rarity, 250)
        if not wallet or wallet.gold < cost:
            LOG.add("Ouro insuficiente para forjar.", (200, 80, 80))
            return
        # Verificar materiais
        for mat_name, qty in self._frg_data["materials"]:
            mat_item = MATERIALS.get(mat_name)
            expected = mat_item().name if mat_item else mat_name
            if self._count_mat_in_bag(expected) < qty:
                LOG.add("Materiais insuficientes.", (200, 80, 80))
                return
        # Tudo ok → consumir materiais e gold
        wallet.gold -= cost
        for mat_name, qty in self._frg_data["materials"]:
            mat_item = MATERIALS.get(mat_name)
            expected = mat_item().name if mat_item else mat_name
            self._remove_mat_from_bag(expected, qty)
        self._frg_result   = self._frg_data["result_factory"]()
        self._frg_complete = True
        LOG.add(f"Forjado: {self._frg_result.name} (-{cost}g)", _COL_GOLD)

    def _do_delete_bag_item(self, idx: int):
        inv = self._inv()
        if inv and 0 <= idx < len(inv.items):
            name = inv.items[idx].name
            inv.items.pop(idx)
            LOG.add(f"Item deletado: {name}", _COL_GREY)

    def _handle_recycle_click(self, mx: int, my: int):
        # Botão Reciclar
        if self._rec_btn_r and self._rec_btn_r.collidepoint(mx, my):
            self._do_recycle()
            return

    def _handle_forge_click(self, mx: int, my: int):
        # Seleção de receita na lista
        for recipe_id, r in self._frg_list_rs:
            if r.collidepoint(mx, my):
                if not self._frg_complete:
                    self._frg_selected = recipe_id
                    self._frg_data     = RECIPES.get(recipe_id)
                    self._frg_result   = None
                return

        # Botão Forjar
        if self._frg_btn_r and self._frg_btn_r.collidepoint(mx, my):
            self._do_forge()
            return

        # Clicar no slot de resultado: loota para bag
        if self._frg_complete and self._frg_result_r and self._frg_result_r.collidepoint(mx, my):
            if self._add_to_bag(self._frg_result):
                LOG.add(f"Lootado: {self._frg_result.name}", _COL_WHITE)
                self._frg_result   = None
                self._frg_complete = False
            else:
                LOG.add("Bag cheia!", _COL_RED)
            return

    def _handle_bag_rclick(self, mx: int, my: int, mode: str):
        # Verifica se clicou em algum slot da bag
        inv = self._inv()
        if not inv:
            return
        scroll = self._bag_scroll_r if mode == "recycle" else self._bag_scroll_f
        for i, r in enumerate(self._bag_item_rs):
            if not r or not r.collidepoint(mx, my):
                continue
            real_idx = scroll * _BAG_COLS + i
            if real_idx >= len(inv.items):
                break
            item = inv.items[real_idx]
            # Modo Reciclar: envia equipamento para slot de reciclagem
            if mode == "recycle":
                if item.item_type in ("weapon", "armor", "shield", "jewelry"):
                    if self._rec_item is not None:
                        # Devolve item anterior para a bag
                        inv.items.insert(self._rec_bag_idx, self._rec_item)
                        # Ajusta índice se necessário
                        real_idx = inv.items.index(item)
                    self._rec_item    = item
                    self._rec_bag_idx = real_idx
                    inv.items.pop(real_idx)
                    return
            # Caso contrário: abre contexto Deletar
            self._ctx_bag_idx = real_idx
            self._ctx_mode    = mode
            self._ctx_pos     = (mx, my)
            return

    # ------------------------------------------------------------------
    # render_world() — indicador "F" acima do ferreiro (antes do fog)
    # ------------------------------------------------------------------
    def render_world(self, cam_x: float, cam_y: float):
        from engine.components import Blacksmith, Visible
        for eid, pos, rend, _ in self.world.get_entities_with(
                Position, Renderable, Blacksmith):
            if not self.world.get_component(eid, Visible):
                continue
            sx = int(pos.x - cam_x)
            sy = int(pos.y - cam_y)
            label = self._font_sm.render("F", False, (255, 200, 80))
            self.world_surf.blit(label, (sx - label.get_width() // 2,
                                     sy - rend.height // 2 - self._u(14)))

    # ------------------------------------------------------------------
    # render() — modal principal (por cima de tudo)
    # ------------------------------------------------------------------
    def render(self):
        self.pending_tooltip = None
        if self._state == self.STATE_CLOSED:
            return
        mx, my = pygame.mouse.get_pos()

        if self._state == self.STATE_MENU:
            self._draw_action_menu(mx, my)
        elif self._state == self.STATE_RECYCLE:
            self._draw_split_modal("Reciclagem", mx, my)
            if self._ctx_bag_idx >= 0:
                self._draw_context_menu(mx, my)
        elif self._state == self.STATE_FORGE:
            self._draw_split_modal("Forja", mx, my)
            if self._ctx_bag_idx >= 0:
                self._draw_context_menu(mx, my)

    # ------------------------------------------------------------------
    # Action menu
    # ------------------------------------------------------------------
    def _draw_action_menu(self, mx: int, my: int):
        BTN_W, BTN_H, BTN_GAP = self._u(220), self._u(34), self._u(6)
        PAD_V = self._u(10)

        options = []
        # Quests disponíveis no NPC
        if self._quest_system and self._bs_eid != -1:
            from engine.components import QuestLog
            from content.quests_data import QUESTS
            qg = self.world.get_component(self._bs_eid, QuestGiver)
            ql = self.world.get_component(self.player_entity, QuestLog)
            if qg and ql:
                for qid in (qg.turn_in_ids or []):
                    if self._quest_system.can_turn_in(qid):
                        qdef = QUESTS.get(qid)
                        if qdef:
                            options.append(("quest:" + qid, f"[?] {qdef.title}", _COL_GOLD))
                for qid in (qg.quest_ids or []):
                    if (qid not in ql.active and qid not in ql.completed
                            and self._quest_system.can_start(qid)):
                        qdef = QUESTS.get(qid)
                        if qdef:
                            options.append(("quest:" + qid, f"[!] {qdef.title}", (220, 160, 40)))

        from engine.components import Blacksmith
        bs = self.world.get_component(self._bs_eid, Blacksmith)
        if bs:
            options.append(("negociar", "Negociar", _COL_WHITE))
        options.append(("reciclar", "Reciclar", _COL_WHITE))
        options.append(("forjar",   "Forjar",   _COL_WHITE))

        total_h = PAD_V * 2 + self._u(30) + (BTN_H + BTN_GAP) * len(options)
        SW, SH  = self.hud_surf.get_size()
        x0      = (SW - BTN_W - PAD_V * 2) // 2
        y0      = (SH - total_h) // 2

        # Fundo
        panel_r = pygame.Rect(x0, y0, BTN_W + PAD_V * 2, total_h)
        pygame.draw.rect(self.hud_surf, _COL_PANEL, panel_r, border_radius=6)
        pygame.draw.rect(self.hud_surf, _COL_BORDER, panel_r, 1, border_radius=6)

        # Título
        title = self._font_md.render(self._bs_name, False, _COL_TITLE)
        self.hud_surf.blit(title, (panel_r.centerx - title.get_width() // 2, y0 + PAD_V))

        # Botão fechar
        close_r = pygame.Rect(panel_r.right - self._u(28), y0 + self._u(4), self._u(24), self._u(24))
        pygame.draw.rect(self.hud_surf, (80, 30, 30), close_r, border_radius=3)
        lbl = self._font_sm.render("X", False, _COL_WHITE)
        self.hud_surf.blit(lbl, (close_r.centerx - lbl.get_width() // 2,
                                close_r.centery - lbl.get_height() // 2))
        self._close_r = close_r

        # Botões de opção
        by = y0 + PAD_V + self._u(30)
        self._menu_rects = {}
        for key, label, col in options:
            r = pygame.Rect(x0 + PAD_V, by, BTN_W, BTN_H)
            hov = r.collidepoint(mx, my)
            pygame.draw.rect(self.hud_surf, (50, 40, 22) if hov else (32, 26, 14),
                             r, border_radius=4)
            pygame.draw.rect(self.hud_surf, _COL_BORDER, r, 1, border_radius=4)
            txt = self._font_md.render(label, False, col)
            self.hud_surf.blit(txt, (r.x + self._u(10), r.centery - txt.get_height() // 2))
            self._menu_rects[key] = r
            by += BTN_H + BTN_GAP

    # ------------------------------------------------------------------
    # Modal dividido (Reciclagem / Forja)
    # ------------------------------------------------------------------
    def _draw_split_modal(self, mode: str, mx: int, my: int):
        x0, y0 = self._panel_origin()

        # Fundo principal
        panel_r = pygame.Rect(x0, y0, self._u(_PANEL_W), self._u(_PANEL_H))
        pygame.draw.rect(self.hud_surf, _COL_PANEL, panel_r)
        pygame.draw.rect(self.hud_surf, _COL_BORDER, panel_r, 1)

        # Divisor vertical
        div_x = x0 + self._u(_LEFT_W)
        pygame.draw.line(self.hud_surf, _COL_BORDER,
                         (div_x, y0 + self._u(4)), (div_x, y0 + self._u(_PANEL_H) - self._u(4)), 1)

        # Botão fechar
        close_r = pygame.Rect(x0 + self._u(_PANEL_W) - self._u(28), y0 + self._u(4), self._u(24), self._u(24))
        pygame.draw.rect(self.hud_surf, (80, 30, 30), close_r, border_radius=3)
        lbl = self._font_sm.render("X", False, _COL_WHITE)
        self.hud_surf.blit(lbl, (close_r.centerx - lbl.get_width() // 2,
                                close_r.centery - lbl.get_height() // 2))
        self._close_r = close_r

        # Conteúdo painel esquerdo
        if mode == "Reciclagem":
            self._draw_recycle_left(x0, y0, mx, my)
        else:
            self._draw_forge_left(x0, y0, mx, my)

        # Conteúdo painel direito (bag grid)
        self._draw_bag_grid(x0, y0, mode, mx, my)

    # ------------------------------------------------------------------
    # Painel esquerdo — Reciclagem
    # ------------------------------------------------------------------
    def _draw_recycle_left(self, x0: int, y0: int, mx: int, my: int):
        cx  = x0 + self._u(_LEFT_W) // 2
        cur = y0 + self._u(_PAD)

        # Título
        t = self._font_lg.render("Reciclagem", False, _COL_TITLE)
        self.hud_surf.blit(t, (cx - t.get_width() // 2, cur))
        cur += self._u(36)

        # Label Item
        lbl = self._font_sm.render("Item", False, _COL_GREY)
        self.hud_surf.blit(lbl, (x0 + self._u(_PAD), cur))
        cur += self._u(18)

        # Slot do item a reciclar
        slot_r = pygame.Rect(x0 + self._u(_PAD), cur, self._u(_ITEM_SLOT), self._u(_ITEM_SLOT))
        self._draw_slot(slot_r, self._rec_item, overlay=False, mx=mx, my=my)
        self._rec_slot_r = slot_r

        # Info do item
        if self._rec_item:
            mats = get_recycle_materials(self._rec_item)
            cost = RARITY_RECYCLE_COST.get(self._rec_item.rarity, 0)
            if not mats:
                info = self._font_sm.render("Nao reciclavel", False, _COL_RED)
                self.hud_surf.blit(info, (slot_r.right + self._u(8), slot_r.y + self._u(6)))
            else:
                info = self._font_sm.render(self._rec_item.name, False,
                                            _RARITY_COL.get(self._rec_item.rarity, _COL_WHITE))
                self.hud_surf.blit(info, (slot_r.right + self._u(8), slot_r.y + self._u(4)))
        cur += self._u(_ITEM_SLOT) + self._u(12)

        # Label Materiais
        lbl = self._font_sm.render("Materiais", False, _COL_GREY)
        self.hud_surf.blit(lbl, (x0 + self._u(_PAD), cur))
        cur += self._u(18)

        # 5 slots — preview dos materiais que serão extraídos
        mat_data = get_recycle_materials(self._rec_item) if self._rec_item else []
        for i in range(5):
            sx = x0 + self._u(_PAD) + i * (self._u(_MAT_SZ) + self._u(6))
            sr = pygame.Rect(sx, cur, self._u(_MAT_SZ), self._u(_MAT_SZ))
            if i < len(mat_data):
                mat_id, qty = mat_data[i]
                preview = MATERIALS[mat_id]() if mat_id in MATERIALS else None
                if preview:
                    preview.stack = qty
                # overlay=False: mostra o item com cor, tooltip funciona
                self._draw_slot(sr, preview, overlay=False, mx=mx, my=my)
            else:
                self._draw_slot(sr, None, overlay=False)
        cur += self._u(_MAT_SZ) + self._u(12)

        # Custo
        if self._rec_item:
            cost = RARITY_RECYCLE_COST.get(self._rec_item.rarity, 0)
            wallet = self._wallet()
            gold   = wallet.gold if wallet else 0
            col    = _COL_WHITE if gold >= cost else _COL_RED
            cost_t = self._font_md.render(f"Custo: {cost}g", False, col)
        else:
            cost_t = self._font_md.render("Custo: —", False, _COL_GREY)
        self.hud_surf.blit(cost_t, (x0 + self._u(_PAD), cur))
        cur += self._u(28)

        # Botão Reciclar
        can_recycle = (self._rec_item is not None
                       and bool(get_recycle_materials(self._rec_item))
                       and self._wallet() is not None
                       and self._wallet().gold >= RARITY_RECYCLE_COST.get(self._rec_item.rarity, 0))
        btn_r = pygame.Rect(x0 + self._u(_PAD), y0 + self._u(_PANEL_H) - self._u(_PAD) - self._u(36),
                            self._u(_LEFT_W) - self._u(_PAD) * 2, self._u(34))
        if can_recycle:
            hov = btn_r.collidepoint(mx, my)
            pygame.draw.rect(self.hud_surf,
                             (60, 130, 60) if hov else (38, 80, 38),
                             btn_r, border_radius=4)
        else:
            pygame.draw.rect(self.hud_surf, (40, 40, 40), btn_r, border_radius=4)
        pygame.draw.rect(self.hud_surf, _COL_BORDER, btn_r, 1, border_radius=4)
        btxt = self._font_md.render("Reciclar", False,
                                    _COL_WHITE if can_recycle else _COL_GREY)
        self.hud_surf.blit(btxt, (btn_r.centerx - btxt.get_width() // 2,
                                btn_r.centery - btxt.get_height() // 2))
        self._rec_btn_r = btn_r

    # ------------------------------------------------------------------
    # Painel esquerdo — Forja
    # ------------------------------------------------------------------
    def _draw_forge_left(self, x0: int, y0: int, mx: int, my: int):
        cx  = x0 + self._u(_LEFT_W) // 2
        cur = y0 + self._u(_PAD)

        # Título
        t = self._font_lg.render("Forja", False, _COL_TITLE)
        self.hud_surf.blit(t, (cx - t.get_width() // 2, cur))
        cur += self._u(36)

        # ── Lista de receitas aprendidas ──────────────────────────────────
        from engine.components import LearnedRecipes
        lr = self.world.get_component(self.player_entity, LearnedRecipes)
        known = lr.known if lr else []

        LIST_ROW_H = self._u(28)
        LIST_W     = self._u(_LEFT_W) - self._u(_PAD) * 2
        # Altura disponível para a lista (reserva espaço para painel inferior)
        BOTTOM_RESERVED = (self._u(36) + self._u(_PAD) * 2 + self._u(_MAT_SZ) + self._u(20)
                            + self._u(_ITEM_SLOT) + self._u(20) + self._u(28) + self._u(36) + self._u(_PAD))
        list_area_h = self._u(_PANEL_H) - (cur - y0) - BOTTOM_RESERVED
        max_vis     = max(1, list_area_h // LIST_ROW_H)
        max_scroll  = max(0, len(known) - max_vis)
        self._frg_list_scroll = min(self._frg_list_scroll, max_scroll)

        lbl = self._font_sm.render("Receitas conhecidas", False, _COL_GREY)
        self.hud_surf.blit(lbl, (x0 + self._u(_PAD), cur))
        cur += self._u(18)

        self._frg_list_rs = []
        if not known:
            empty = self._font_sm.render("Nenhuma receita aprendida.", False, _COL_GREY)
            self.hud_surf.blit(empty, (x0 + self._u(_PAD), cur))
        else:
            for vis_i in range(max_vis):
                real_i = vis_i + self._frg_list_scroll
                if real_i >= len(known):
                    break
                recipe_id  = known[real_i]
                recipe_def = RECIPES.get(recipe_id)
                if not recipe_def:
                    continue
                row_r   = pygame.Rect(x0 + self._u(_PAD), cur + vis_i * LIST_ROW_H, LIST_W, LIST_ROW_H - self._u(2))
                selected = (recipe_id == self._frg_selected)
                hov      = row_r.collidepoint(mx, my)
                if selected:
                    pygame.draw.rect(self.hud_surf, (70, 55, 18), row_r, border_radius=3)
                    pygame.draw.rect(self.hud_surf, _COL_GOLD, row_r, 1, border_radius=3)
                elif hov:
                    pygame.draw.rect(self.hud_surf, (45, 36, 14), row_r, border_radius=3)
                rarity    = recipe_def.get("result_rarity", "common")
                name_col  = _RARITY_COL.get(rarity, _COL_WHITE)
                name_t    = self._font_sm.render(recipe_def["name"], False, name_col)
                self.hud_surf.blit(name_t, (row_r.x + self._u(6), row_r.centery - name_t.get_height() // 2))
                self._frg_list_rs.append((recipe_id, row_r))

            # Scrollbar da lista
            if len(known) > max_vis:
                sb_h = max_vis * LIST_ROW_H
                th   = max(self._u(14), sb_h * max_vis // len(known))
                ty   = cur + (sb_h - th) * self._frg_list_scroll // max(1, max_scroll)
                sb_x = x0 + self._u(_LEFT_W) - self._u(8)
                pygame.draw.rect(self.hud_surf, (40, 34, 18), (sb_x, cur, self._u(4), sb_h), border_radius=2)
                pygame.draw.rect(self.hud_surf, _COL_BORDER,  (sb_x, ty,  self._u(4), th),  border_radius=2)

        cur += max_vis * LIST_ROW_H + self._u(8)

        # ── Materiais necessários (da receita selecionada) ─────────────────
        lbl = self._font_sm.render("Materiais", False, _COL_GREY)
        self.hud_surf.blit(lbl, (x0 + self._u(_PAD), cur))
        cur += self._u(18)

        self._frg_mat_rs = []
        mat_list = self._frg_data["materials"] if self._frg_data else []
        for i in range(5):
            sx = x0 + self._u(_PAD) + i * (self._u(_MAT_SZ) + self._u(6))
            sr = pygame.Rect(sx, cur, self._u(_MAT_SZ), self._u(_MAT_SZ))
            if i < len(mat_list):
                mat_id, req_qty = mat_list[i]
                mat_item = MATERIALS.get(mat_id, lambda: None)()
                have = self._count_mat_in_bag(mat_item.name) if mat_item else 0
                sufficient = have >= req_qty
                if mat_item:
                    mat_item.stack = req_qty
                self._draw_slot(sr, mat_item, overlay=not sufficient, mx=mx, my=my)
                if mat_item:
                    qty_col = _COL_GREEN if sufficient else _COL_RED
                    qty_t   = self._font_sm.render(f"{have}/{req_qty}", False, qty_col)
                    self.hud_surf.blit(qty_t, (sr.x + sr.w // 2 - qty_t.get_width() // 2,
                                             sr.bottom + self._u(2)))
            else:
                self._draw_slot(sr, None, overlay=False)
            self._frg_mat_rs.append(sr)
        cur += self._u(_MAT_SZ) + self._u(20)

        # ── Resultado ─────────────────────────────────────────────────────
        lbl = self._font_sm.render("Resultado", False, _COL_GREY)
        self.hud_surf.blit(lbl, (x0 + self._u(_PAD), cur))
        cur += self._u(18)

        res_r = pygame.Rect(x0 + self._u(_PAD), cur, self._u(_ITEM_SLOT), self._u(_ITEM_SLOT))
        if self._frg_data and not self._frg_complete:
            preview = self._frg_data["result_factory"]()
            self._draw_slot(res_r, preview, overlay=True, mx=mx, my=my)
        elif self._frg_complete and self._frg_result:
            self._draw_slot(res_r, self._frg_result, overlay=False, mx=mx, my=my)
            hint = self._font_sm.render("Clicar para pegar", False, _COL_GREY)
            self.hud_surf.blit(hint, (res_r.right + self._u(6), res_r.y + self._u(6)))
        else:
            self._draw_slot(res_r, None, overlay=False)
        self._frg_result_r = res_r
        cur += self._u(_ITEM_SLOT) + self._u(8)

        # ── Custo ─────────────────────────────────────────────────────────
        if self._frg_data:
            rarity = self._frg_data.get("result_rarity", "common")
            cost   = RARITY_FORGE_COST.get(rarity, 0)
            wallet = self._wallet()
            gold   = wallet.gold if wallet else 0
            col    = _COL_WHITE if gold >= cost else _COL_RED
            cost_t = self._font_md.render(f"Custo: {cost}g", False, col)
        else:
            cost_t = self._font_md.render("Custo: —", False, _COL_GREY)
        self.hud_surf.blit(cost_t, (x0 + self._u(_PAD), cur))

        # ── Botão Forjar ──────────────────────────────────────────────────
        can_forge = (
            self._frg_data is not None
            and not self._frg_complete
            and self._wallet() is not None
            and self._wallet().gold >= RARITY_FORGE_COST.get(
                self._frg_data.get("result_rarity", "common"), 0)
            and all(
                self._count_mat_in_bag(
                    (MATERIALS[mid]() if mid in MATERIALS else type('X', (), {'name': mid})()).name
                ) >= qty
                for mid, qty in mat_list
            )
        )
        btn_r = pygame.Rect(x0 + self._u(_PAD), y0 + self._u(_PANEL_H) - self._u(_PAD) - self._u(36),
                            self._u(_LEFT_W) - self._u(_PAD) * 2, self._u(34))
        if can_forge:
            hov = btn_r.collidepoint(mx, my)
            pygame.draw.rect(self.hud_surf,
                             (100, 80, 30) if hov else (70, 55, 18),
                             btn_r, border_radius=4)
        else:
            pygame.draw.rect(self.hud_surf, (40, 40, 40), btn_r, border_radius=4)
        pygame.draw.rect(self.hud_surf, _COL_BORDER, btn_r, 1, border_radius=4)
        btxt = self._font_md.render("Forjar", False,
                                    _COL_GOLD if can_forge else _COL_GREY)
        self.hud_surf.blit(btxt, (btn_r.centerx - btxt.get_width() // 2,
                                btn_r.centery - btxt.get_height() // 2))
        self._frg_btn_r = btn_r

    # ------------------------------------------------------------------
    # Painel direito — grid da bag
    # ------------------------------------------------------------------
    def _draw_bag_grid(self, x0: int, y0: int, mode: str, mx: int, my: int):
        rx    = x0 + self._u(_LEFT_W) + self._u(_DIVIDER)
        cur   = y0 + self._u(_PAD)
        inv   = self._inv()
        items = inv.items if inv else []

        scroll = self._bag_scroll_r if mode == "Reciclagem" else self._bag_scroll_f

        # Título
        t = self._font_lg.render("bag", False, _COL_TITLE)
        self.hud_surf.blit(t, (rx + (self._u(_RIGHT_W) - t.get_width()) // 2, cur))
        cur += self._u(36)

        # Clamp scroll
        bag_row_h  = self._u(_SLOT_SZ) + self._u(_SLOT_GAP)
        total_rows = max(0, (len(items) - 1) // _BAG_COLS + 1) if items else 0
        avail_h    = self._u(_PANEL_H) - self._u(36) - self._u(_PAD) * 2
        max_vis    = avail_h // bag_row_h
        max_scroll = max(0, total_rows - max_vis)
        if mode == "Reciclagem":
            self._bag_scroll_r = min(scroll, max_scroll)
            scroll = self._bag_scroll_r
        else:
            self._bag_scroll_f = min(scroll, max_scroll)
            scroll = self._bag_scroll_f

        # Grid de slots
        self._bag_item_rs = []
        for row in range(max_vis):
            for col in range(_BAG_COLS):
                real_idx = (scroll + row) * _BAG_COLS + col
                sx = rx + self._u(_PAD) + col * (self._u(_SLOT_SZ) + self._u(_SLOT_GAP))
                sy = cur + row * bag_row_h
                sr = pygame.Rect(sx, sy, self._u(_SLOT_SZ), self._u(_SLOT_SZ))
                item = items[real_idx] if real_idx < len(items) else None
                self._draw_slot(sr, item, overlay=False, show_stack=True, mx=mx, my=my)
                if len(self._bag_item_rs) <= row * _BAG_COLS + col:
                    self._bag_item_rs.append(sr)
                else:
                    self._bag_item_rs[row * _BAG_COLS + col] = sr

        # Scrollbar
        if total_rows > max_vis and total_rows > 0:
            sb_x  = rx + self._u(_RIGHT_W) - self._u(10)
            sb_h  = max_vis * bag_row_h
            th    = max(self._u(20), sb_h * max_vis // total_rows)
            ty    = cur + (sb_h - th) * scroll // max(1, max_scroll)
            pygame.draw.rect(self.hud_surf, (40, 34, 18), (sb_x, cur, self._u(5), sb_h), border_radius=2)
            pygame.draw.rect(self.hud_surf, _COL_BORDER,  (sb_x, ty,  self._u(5), th),  border_radius=2)

    # ------------------------------------------------------------------
    # Slot genérico
    # ------------------------------------------------------------------
    def _draw_slot(self, rect: pygame.Rect, item, overlay: bool = False,
                   show_stack: bool = False, mx: int = -1, my: int = -1):
        pygame.draw.rect(self.hud_surf, _COL_DARK, rect)
        pygame.draw.rect(self.hud_surf, _COL_BORDER, rect, 1)
        if item is None:
            return
        icon_r = rect.inflate(-self._u(8), -self._u(8))
        col    = _RARITY_COL.get(item.rarity, (180, 180, 180))
        pygame.draw.rect(self.hud_surf, col, icon_r, border_radius=2)
        # Letra inicial do item (placeholder visual)
        letter = self._font_md.render(item.name[0].upper(), False, _COL_DARK)
        self.hud_surf.blit(letter, (icon_r.centerx - letter.get_width() // 2,
                                  icon_r.centery - letter.get_height() // 2))
        if overlay:
            self.hud_surf.blit(fill_surf(icon_r.size, (0, 0, 0, 160)), icon_r)
        if show_stack:
            from ui.ui_helpers import draw_stack_count
            draw_stack_count(self.hud_surf, item, rect, self._font_sm)
        # Tooltip ao hover
        if mx >= 0 and rect.collidepoint(mx, my):
            from engine.components import CharacterStats as _CharCraft
            _char_craft = self.world.get_component(self.player_entity, _CharCraft)
            lines = item_tooltip_lines(item, _char_craft.class_id if _char_craft else None)
            equip = self.world.get_component(self.player_entity, Equipment)
            eq_item = equip.slots.get(item.slot) if equip and item.slot else None
            name_col = _RARITY_COL.get(item.rarity, (255, 220, 100))
            self.pending_tooltip = (mx, my, item.name, lines, name_col, item, eq_item)

    # ------------------------------------------------------------------
    # Contexto "Deletar"
    # ------------------------------------------------------------------
    def _draw_context_menu(self, mx: int, my: int):
        inv = self._inv()
        if not inv:
            return
        scroll = (self._bag_scroll_r if self._ctx_mode == "recycle"
                  else self._bag_scroll_f)
        idx = self._ctx_bag_idx
        if idx < 0 or idx >= len(inv.items):
            self._ctx_bag_idx = -1
            return

        item = inv.items[idx]
        W, H = self._u(140), self._u(62)
        cx, cy = self._ctx_pos
        # Mantém dentro da tela
        SW, SH = self.hud_surf.get_size()
        if cx + W > SW:
            cx = SW - W - self._u(4)
        if cy + H > SH:
            cy = SH - H - self._u(4)

        bg_r = pygame.Rect(cx, cy, W, H)
        pygame.draw.rect(self.hud_surf, _COL_PANEL, bg_r, border_radius=4)
        pygame.draw.rect(self.hud_surf, _COL_BORDER, bg_r, 1, border_radius=4)

        name_t = self._font_sm.render(item.name[:16], False,
                                      _RARITY_COL.get(item.rarity, _COL_WHITE))
        self.hud_surf.blit(name_t, (cx + self._u(6), cy + self._u(6)))

        del_r = pygame.Rect(cx + self._u(6), cy + self._u(28), W - self._u(12), self._u(26))
        hov   = del_r.collidepoint(mx, my)
        pygame.draw.rect(self.hud_surf, (100, 30, 30) if hov else (65, 20, 20),
                         del_r, border_radius=3)
        pygame.draw.rect(self.hud_surf, _COL_BORDER, del_r, 1, border_radius=3)
        dt = self._font_sm.render("Deletar", False, _COL_WHITE)
        self.hud_surf.blit(dt, (del_r.centerx - dt.get_width() // 2,
                               del_r.centery - dt.get_height() // 2))
        self._ctx_del_r = del_r
