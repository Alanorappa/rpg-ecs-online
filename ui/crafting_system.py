# crafting_system.py
"""
BlacksmithSystem — gerencia interação com NPCs ferreiros.

Fluxo:
  1. Clique direito em NPC com componente Blacksmith → action menu
  2. Action menu: Negociar / Reciclar / Forjar / quests disponíveis
  3. Cada opção abre o modal correspondente (Reciclar/Forjar = 1 janela
     com abas, atlas de UI — ui/atlas_window.py, cliques no SOLTAR)
"""
from __future__ import annotations
import math
import pygame
from ui.fonts import make as _font
from ui.systems import System
from ui.ui_scale_mixin import UIScaleMixin
from engine.components import (Position, Renderable, TileMovement,
                        CombatState, Inventory, Wallet, NPC, Equipment,
                        NPCInteractionRequest)
from content.crafting_data import (MATERIALS, RECYCLE_TABLE, RECIPES, RECIPE_ITEMS,
                           RARITY_RECYCLE_COST, RARITY_FORGE_COST,
                           get_recycle_materials)
from ui.combat_log import LOG
from engine.tileset import TILE_SIZE
from engine.utils import get_camera_offset, chebyshev
import engine.quest_logic as quest_logic
from ui import atlas_window as aw
from ui import ui_atlas
from ui.ui_helpers import item_tooltip_lines
from ui.npc_interaction import walk_to_npc, suppress_chase


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

# Layout: atlas de UI (21/10/2026), tamanhos em tiles calculados em
# BlacksmithSystem._craft_layout — não mais em ui_sizes.py.


class BlacksmithSystem(UIScaleMixin, System):
    """Gerencia o menu de ação e os modais de Reciclagem e Forja."""

    # Estado da máquina de estados
    STATE_CLOSED  = "CLOSED"
    STATE_MENU    = "ACTION_MENU"
    STATE_RECYCLE = "RECYCLE"
    STATE_FORGE   = "FORGE"

    _FONT_BASES = {"_font_xs": 15, "_font_sm": 20, "_font_md": 26, "_font_lg": 32}

    def __init__(self, world, player_entity, screen):
        super().__init__()
        self.world          = world
        self.player_entity  = player_entity
        self.hud_surf         = screen
        # Débito A4 (11/08/2026, ver PROBLEMAS_ARQUITETURA.md) — envia
        # CRAFT_REQUEST/RECYCLE_REQUEST ao servidor em vez de mutar ouro/
        # mochila só localmente. Injetado pelo GameEngine (game.py), None
        # = modo sem rede (nunca deveria acontecer em produção, mas evita
        # crash se algo chamar antes da conexão).
        self._net = None

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
        self._rec_pending  = False  # RECYCLE_REQUEST enviado, aguardando servidor

        # --- Estado: Forge ---
        self._frg_selected = None   # recipe_id selecionado na lista
        self._frg_data     = None   # RECIPES[recipe_id]
        self._frg_pending  = False  # CRAFT_REQUEST enviado, aguardando servidor

        # --- Contexto de bag (menu "Deletar") ---
        self._ctx_bag_idx  = -1
        self._ctx_mode     = ""     # "recycle" ou "forge"
        self._ctx_pos      = (0, 0)

        # Atlas de UI: botão afundado, rolagem da lista de receitas, eventos
        # do frame (guardados em handle_events, consumidos em render), área
        # segura (injetada por game.py), rects clicáveis (testes).
        self._press = aw.PressState()
        self._frg_scroll = aw.ScrollState()
        self._frame_events: list = []
        self.safe_area_fn = None
        self._hit_rects: dict = {}
        self._post_draw = None

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
        return chebyshev(a[0], a[1], b[0], b[1])

    # ------------------------------------------------------------------
    # Bag helpers
    # ------------------------------------------------------------------
    def _inv(self):
        return self.world.get_component(self.player_entity, Inventory)

    def _wallet(self):
        return self.world.get_component(self.player_entity, Wallet)

    def _count_mat_in_bag(self, mat_id: str) -> int:
        """`mat_id` (débito C2, 10/08/2026) — chave de MATERIALS
        (ex.: "fragmento_ferro"), a mesma que RECIPES[...]["materials"]
        já usa; era nome de exibição antes, exigindo instanciar a
        factory só pra comparar."""
        inv = self._inv()
        if not inv:
            return 0
        return sum(it.stack for it in inv.items if it is not None and it.item_id == mat_id)

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

        # ESC fecha qualquer estado (menu "Deletar" primeiro)
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
                    _wsurf = self.world_surf or self.hud_surf
                    SW, SH = _wsurf.get_size()
                    cx, cy = get_camera_offset(self.world, SW, SH)
                    _sc = _wsurf.get_width() / max(1, self.hud_surf.get_width())
                    wx = ev.pos[0] * _sc + cx
                    wy = ev.pos[1] * _sc + cy
                    hw = rend.width  / 2
                    hh = rend.height / 2
                    if abs(wx - pos.x) <= hw and abs(wy - pos.y) <= hh:
                        pt = self._player_tile()
                        bt = self._bs_tile(eid)
                        if pt and bt and self._cheby(pt, bt) <= 1:
                            suppress_chase(self.world, self.player_entity)
                            self._open_menu(eid)
                        else:
                            self._pending_bs_id = eid
                            walk_to_npc(self.world, self.player_entity, pt, bt)
                        self._right_click_consumed = True
                        break
                if self._right_click_consumed:
                    break

    # ------------------------------------------------------------------
    # handle_events() — chamado pelo game.py quando is_open. Atlas de UI
    # (21/10/2026): cliques agem no SOLTAR dentro de render(); aqui só se
    # guardam os eventos do frame.
    # ------------------------------------------------------------------
    def handle_events(self, events):
        self._frame_events = list(events)

    # ------------------------------------------------------------------
    # Ações internas
    # ------------------------------------------------------------------
    def _open_menu(self, eid: int):
        """Abre o menu do ferreiro, OU — se travado por quest (06/10/2026,
        generalização pedida pelo usuário) — pede pro QuestDialogSystem
        abrir no lugar, mesmo padrão de `ui/shop_system.py::
        _try_open_merchant`/`ui/trainer_system.py::_open_menu`. Cobre
        negociar/reciclar/forjar de uma vez (os 3 só são alcançáveis
        depois de passar por aqui, único ponto de entrada)."""
        from engine.components import QuestLog as _QL_bs
        ql = self.world.get_component(self.player_entity, _QL_bs)
        if not quest_logic.is_npc_unlocked(self.world, eid, ql):
            self.world.add_component(self.player_entity, NPCInteractionRequest(
                kind="open_quest_dialog", target_eid=eid))
            self._open_cooldown = 0.3
            return
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
        self._rec_pending = False

    def _reset_forge(self):
        self._frg_selected = None
        self._frg_data     = None
        self._frg_pending  = False

    def _do_negociar(self):
        self.world.add_component(self.player_entity, NPCInteractionRequest(
            kind="open_shop", target_eid=self._bs_eid))
        self._state = self.STATE_CLOSED

    def _do_recycle(self):
        """Manda RECYCLE_REQUEST — débito A4 (11/08/2026, ver
        PROBLEMAS_ARQUITETURA.md). Servidor confere tipo/ouro reais no
        item da posição indicada e é quem desconta+credita de verdade;
        aqui só valida localmente pra feedback imediato (mesmo padrão de
        _equip_item: UX otimista, autoridade real no servidor)."""
        if self._rec_item is None or self._rec_pending:
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
        if not self._net:
            return
        from shared.messages import MsgType as _MTRec
        self._rec_pending = True
        self._net.send(_MTRec.RECYCLE_REQUEST, {
            "inv_index": self._rec_bag_idx, "npc_eid": self._bs_eid})

    def _do_forge(self):
        """Manda CRAFT_REQUEST — débito A4 (11/08/2026, ver
        PROBLEMAS_ARQUITETURA.md). Servidor lê a receita do próprio
        catálogo e confere ouro/material reais no Inventory ao vivo antes
        de aplicar; aqui só valida localmente pra feedback imediato
        (mesmo padrão de _equip_item). Resultado cai direto na mochila —
        1 clique, sem passo de "coletar" (simplificação deliberada,
        decisão do usuário, mesmo padrão de BUY_REQUEST/BUY_RESULT)."""
        if not self._frg_data or self._frg_pending:
            return
        wallet = self._wallet()
        rarity = self._frg_data.get("result_rarity", "common")
        cost   = RARITY_FORGE_COST.get(rarity, 250)
        if not wallet or wallet.gold < cost:
            LOG.add("Ouro insuficiente para forjar.", (200, 80, 80))
            return
        for mat_id, qty in self._frg_data["materials"]:
            if self._count_mat_in_bag(mat_id) < qty:
                LOG.add("Materiais insuficientes.", (200, 80, 80))
                return
        if not self._net:
            return
        from shared.messages import MsgType as _MTCraft
        self._frg_pending = True
        self._net.send(_MTCraft.CRAFT_REQUEST, {
            "recipe_id": self._frg_selected, "npc_eid": self._bs_eid})

    def _do_delete_bag_item(self, idx: int):
        # ATENÇÃO (21/10/2026): remove só do espelho LOCAL — sem INV_SYNC
        # (ver CLAUDE.md, "mutação local de Inventory"), o item volta no
        # próximo resync. Comportamento pré-existente, não corrigido na
        # troca de visual — decisão pendente com o usuário.
        inv = self._inv()
        if inv and 0 <= idx < len(inv.items):
            name = inv.items[idx].name
            inv.items.pop(idx)
            LOG.add(f"Item deletado: {name}", _COL_GREY)

    def _bag_rclick(self, real_idx: int, mode: str, screen_pos) -> None:
        """Clique direito num item da mochila. Reciclar: equipamento vai
        pro slot de reciclagem (bloqueado com RECYCLE_REQUEST pendente —
        trocar o item nesse meio-tempo dessincronizaria a posição que o
        servidor já está processando, débito A4 11/08/2026). Caso
        contrário: menu "Deletar"."""
        inv = self._inv()
        if not inv or not 0 <= real_idx < len(inv.items):
            return
        item = inv.items[real_idx]
        if mode == "recycle" and not self._rec_pending:
            if item.item_type in ("weapon", "armor", "shield", "jewelry"):
                if self._rec_item is not None:
                    # Devolve item anterior para a bag
                    inv.items.insert(self._rec_bag_idx, self._rec_item)
                    real_idx = inv.items.index(item)
                self._rec_item    = item
                self._rec_bag_idx = real_idx
                inv.items.pop(real_idx)
                return
        self._ctx_bag_idx = real_idx
        self._ctx_mode    = mode
        self._ctx_pos     = screen_pos

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
    # render() — atlas de UI (21/10/2026)
    # ------------------------------------------------------------------
    # Menu de ação: janela escura pequena (nome do NPC, X, botões
    # empilhados). Reciclar/Forjar: 1 janela com barra de título e as abas
    # [Reciclar] [Forjar]; esquerda = conteúdo da aba, direita = mochila em
    # grade. Cliques no SOLTAR (atlas_window); direito na mochila envia pra
    # reciclagem ou abre o menu "Deletar".

    _BAG_COLS, _BAG_ROWS = 5, 4
    _LIST_ROWS = 4          # receitas visíveis antes de rolar

    def _area(self) -> pygame.Rect:
        if self.safe_area_fn is not None:
            return self.safe_area_fn()
        SW, SH = self.hud_surf.get_size()
        return aw.safe_area(SW, SH, SH)

    def _hit(self, hid: str, f, x0, y0, x1, y1) -> None:
        self._hit_rects[hid] = pygame.Rect(f.px + round(x0 * aw.S), f.py + round(y0 * aw.S),
                                           round((x1 - x0) * aw.S), round((y1 - y0) * aw.S))

    def render(self):
        events, self._frame_events = self._frame_events, []
        self.pending_tooltip = None
        self._hit_rects = {}
        if self._state == self.STATE_CLOSED:
            return
        if self._state == self.STATE_MENU:
            self._draw_action_menu(events)
        else:
            ctx = self._ctx_bag_idx >= 0
            self._draw_craft_window([] if ctx else events, ctx)
            if ctx:
                self._draw_context_menu(events)

    # ── Menu de ação ──────────────────────────────────────────────────
    def _draw_action_menu(self, events):
        T = aw.T
        options = []
        # Quests disponíveis no NPC — via engine.quest_logic (ponto único
        # de verdade, ver PROBLEMAS_ARQUITETURA.md item 4/§139).
        if self._bs_eid != -1:
            from content.quests_data import QUESTS
            for qid in quest_logic.get_completable_quests(self.world, self.player_entity, self._bs_eid):
                qdef = QUESTS.get(qid)
                if qdef:
                    options.append(("quest:" + qid, f"[?] {qdef.title}"))
            for qid in quest_logic.get_available_quests(self.world, self.player_entity, self._bs_eid):
                qdef = QUESTS.get(qid)
                if qdef:
                    options.append(("quest:" + qid, f"[!] {qdef.title}"))
        from engine.components import Blacksmith
        if self.world.get_component(self._bs_eid, Blacksmith):
            options.append(("negociar", "Negociar"))
        options.append(("reciclar", "Reciclar"))
        options.append(("forjar", "Forjar"))

        bw = aw.tiles_for(self._font_md, [lbl for _, lbl in options], 5)
        tw = max(bw + 2, aw.tiles_for(self._font_md, [self._bs_name], 1) + 3)
        th = 1 + len(options) + 1
        f = aw.Frame(self.hud_surf, events, tw, th, area=self._area(), press=self._press,
                     font=self._font_md, raw=ui_atlas.titled_dark_window_raw(tw, th))
        f.text(self._bs_name, (tw - 1) * T / 2, aw.TITLE_BAR_Y, (240, 228, 200))
        if f.close_button("bs:close", tw - 1):
            self._close()
            return
        self._hit("bs:close", f, (tw - 1) * T, 0, tw * T, T)
        col = (tw - bw) // 2
        ay0, ay1 = ui_atlas.button_art_y(False)
        chosen = None
        for i, (key, label) in enumerate(options):
            if f.button(f"bs:{key}", col, 1 + i, bw, label, tall=False):
                chosen = key
            self._hit(f"bs:{key}", f, col * T, (1 + i) * T + ay0, (col + bw) * T, (1 + i) * T + ay1)
        f.end()
        if chosen == "negociar":
            self._do_negociar()
        elif chosen == "reciclar":
            self._state = self.STATE_RECYCLE
            self._reset_recycle()
        elif chosen == "forjar":
            self._state = self.STATE_FORGE
            self._reset_forge()
        elif chosen and chosen.startswith("quest:"):
            self.world.add_component(self.player_entity, NPCInteractionRequest(
                kind="open_quest_dialog", target_eid=self._bs_eid))
            self._state = self.STATE_CLOSED

    # ── Janela Reciclar/Forjar ────────────────────────────────────────
    def _craft_layout(self) -> dict:
        T, S = aw.T, aw.S
        from engine.components import LearnedRecipes
        lr = self.world.get_component(self.player_entity, LearnedRecipes)
        known = [r for r in (lr.known if lr else []) if r in RECIPES]
        names = [RECIPES[r]["name"] for r in known] + ["Nenhuma receita aprendida.",
                                                       "Receitas conhecidas", "Custo: 9999g"]
        text_w = max(self._font_sm.size(n)[0] for n in names) / S
        left_cols = max(10, math.ceil((text_w + 16) / T) + 1, 5 * 1 + 6)
        tw = 1 + left_cols + 1 + self._BAG_COLS + 1
        th = 14
        return {"tw": tw, "th": th, "left_cols": left_cols, "bag_col0": tw - 1 - self._BAG_COLS,
                "known": known}

    def _draw_craft_window(self, events, ctx_open: bool):
        T, S = aw.T, aw.S
        lay = self._craft_layout()
        tw, th = lay["tw"], lay["th"]
        f = aw.Frame(self.hud_surf, events, tw, th, area=self._area(),
                     press=None if ctx_open else self._press, font=self._font_md,
                     raw=ui_atlas.titled_dark_window_raw(tw, th))
        f.text(self._bs_name, (tw - 1) * T / 2, aw.TITLE_BAR_Y, (240, 228, 200))
        if f.close_button("bs:close", tw - 1):
            self._close()
            return
        self._hit("bs:close", f, (tw - 1) * T, 0, tw * T, T)
        self._panel_rect = pygame.Rect(f.px, f.py, f.pw, f.ph)

        self._post_draw = None
        recycle = self._state == self.STATE_RECYCLE
        tab = f.tabs("bs:tabs", 1, 1, ["Reciclar", "Forjar"], 0 if recycle else 1, font=self._font_sm)
        w0 = f.tiles_for(["Reciclar"], 3, self._font_sm)
        w1 = f.tiles_for(["Forjar"], 3, self._font_sm)
        self._hit("bs:tab:0", f, T, T, (1 + w0) * T, 2 * T)
        self._hit("bs:tab:1", f, (1 + w0) * T, T, (1 + w0 + w1) * T, 2 * T)
        f.divider_v((lay["bag_col0"] - 1) * T + T // 2, 2 * T, (th - 1) * T)

        if recycle:
            self._draw_recycle_left(f, lay)
        else:
            self._draw_forge_left(f, lay)
        self._draw_bag(f, lay, "recycle" if recycle else "forge")
        f.end()
        if self._post_draw is not None:
            self._post_draw()
            self._post_draw = None

        if tab == 0 and not recycle:
            self._state = self.STATE_RECYCLE
            self._reset_recycle()
        elif tab == 1 and recycle:
            self._state = self.STATE_FORGE
            self._reset_forge()

    def _slot_tooltip(self, item) -> None:
        if item is None:
            return
        from engine.components import CharacterStats as _CharCraft
        char = self.world.get_component(self.player_entity, _CharCraft)
        lines = item_tooltip_lines(item, char.class_id if char else None)
        equip = self.world.get_component(self.player_entity, Equipment)
        eq_item = equip.slots.get(item.slot) if equip and item.slot else None
        mx, my = pygame.mouse.get_pos()
        self.pending_tooltip = (mx, my, item.name, lines, _RARITY_COL.get(item.rarity, (255, 220, 100)),
                                item, eq_item)

    def _cost_line(self, f, row: int, cost) -> None:
        T = aw.T
        if cost is None:
            f.text("Custo: —", T, row * T + T / 2, _COL_GREY, self._font_sm, anchor="midleft")
            return
        wallet = self._wallet()
        ok = wallet is not None and wallet.gold >= cost
        f.text(f"Custo: {cost}g", T, row * T + T / 2, _COL_WHITE if ok else _COL_RED,
               self._font_sm, anchor="midleft")

    def _action_button(self, f, lay, label: str, enabled: bool) -> bool:
        T = aw.T
        bw = f.tiles_for([label], 4)
        col = 1 + (lay["left_cols"] - bw) // 2
        fired = f.button(f"bs:{label}", col, lay["th"] - 2, bw, label, enabled=enabled)
        ay0, ay1 = ui_atlas.button_art_y(True)
        self._hit(f"bs:{label}", f, col * T, (lay["th"] - 2) * T + ay0, (col + bw) * T, (lay["th"] - 2) * T + ay1)
        return fired

    def _draw_recycle_left(self, f, lay):
        T = aw.T
        f.text("Item", T, 2.5 * T, _COL_GREY, self._font_sm, anchor="midleft")
        hov, _ = f.slot("bs:rec:item", T, 3 * T, aw.item_icon(self._rec_item, self._font_md))
        if hov:
            self._slot_tooltip(self._rec_item)
        mats = get_recycle_materials(self._rec_item) if self._rec_item else []
        if self._rec_item is not None:
            if not mats:
                f.text("Não reciclável", 2 * T + 6, 3.5 * T, _COL_RED, self._font_sm, anchor="midleft")
            else:
                f.text(self._rec_item.name, 2 * T + 6, 3.5 * T,
                       _RARITY_COL.get(self._rec_item.rarity, _COL_WHITE), self._font_sm, anchor="midleft")
        f.text("Materiais", T, 5.5 * T, _COL_GREY, self._font_sm, anchor="midleft")
        for i in range(5):
            preview = None
            if i < len(mats):
                mat_id, qty = mats[i]
                preview = MATERIALS[mat_id]() if mat_id in MATERIALS else None
                if preview:
                    preview.stack = qty
            hov, _ = f.slot(f"bs:rec:mat:{i}", T + i * (T + 2), 6 * T, aw.item_icon(preview, self._font_md),
                            preview.stack if preview and preview.stack > 1 else 0, dark=True,
                            count_font=self._font_xs)
            if hov:
                self._slot_tooltip(preview)
        self._cost_line(f, 8, RARITY_RECYCLE_COST.get(self._rec_item.rarity, 0) if self._rec_item else None)
        wallet = self._wallet()
        can = (self._rec_item is not None and not self._rec_pending and bool(mats)
               and wallet is not None and wallet.gold >= RARITY_RECYCLE_COST.get(self._rec_item.rarity, 0))
        if self._action_button(f, lay, "Reciclar", can):
            self._do_recycle()

    def _draw_forge_left(self, f, lay):
        T, S = aw.T, aw.S
        known = lay["known"]
        f.text("Receitas conhecidas", T, 2.5 * T, _COL_GREY, self._font_sm, anchor="midleft")
        lw = lay["left_cols"] * T - T      # deixa 1 coluna pra barra de rolagem
        view_y0, view_h = 3 * T, self._LIST_ROWS * T
        content_h = max(1, len(known) * T)
        off = f.scrollbar(self._frg_scroll, lay["left_cols"], 3, 3 + self._LIST_ROWS - 1,
                          max(0, content_h - view_h))
        in_view = view_y0 <= f.ry < view_y0 + view_h and T <= f.rx < T + lw
        fc = f.child(pygame.Surface((lw, content_h), pygame.SRCALPHA),
                     f.ry - view_y0 + off if in_view else -10_000)
        fc.rx = f.rx - T
        if not known:
            f.text("Nenhuma receita aprendida.", T, 3.5 * T, _COL_GREY, self._font_sm, anchor="midleft")
        picked = None
        for i, rid in enumerate(known):
            rdef = RECIPES[rid]
            y = i * T
            sel = rid == self._frg_selected
            hov = fc.hover(0, y, lw, y + T)
            col = aw.ACTIVE_COL if sel else (_RARITY_COL.get(rdef.get("result_rarity", "common"), _COL_WHITE))
            fc.text(("> " if sel else "") + rdef["name"], 4, y + T / 2, col if not hov or sel else aw.TEXT_COL,
                    self._font_sm, anchor="midleft")
            if fc.press(f"bs:recipe:{i}", 0, y, lw, y + T)[1] and not self._frg_pending:
                picked = rid
            ry = view_y0 + y - off
            if view_y0 <= ry and ry + T <= view_y0 + view_h:
                self._hit(f"bs:recipe:{i}", f, T, ry, T + lw, ry + T)
        if known:
            def content():
                surf = ui_atlas.scale_up(fc.raw)
                aw.draw_texts(surf, fc.texts, self._font_sm)
                view = surf.subsurface((0, off * S, surf.get_width(), max(0, min(view_h, content_h - off)) * S))
                self.hud_surf.blit(view, (f.px + T * S, f.py + view_y0 * S))
            self._post_draw = content   # desenhado depois do painel (f.end)

        mat_list = self._frg_data["materials"] if self._frg_data else []
        f.text("Materiais", T, 7.5 * T, _COL_GREY, self._font_sm, anchor="midleft")
        for i in range(5):
            sx = T + i * (T + 4)
            mat_item, have, req = None, 0, 0
            if i < len(mat_list):
                mat_id, req = mat_list[i]
                mat_item = MATERIALS.get(mat_id, lambda: None)()
                have = self._count_mat_in_bag(mat_id) if mat_item else 0
                if mat_item:
                    mat_item.stack = req
            hov, _ = f.slot(f"bs:frg:mat:{i}", sx, 8 * T, aw.item_icon(mat_item, self._font_md),
                            dark=True, dim=mat_item is not None and have < req)
            if mat_item is not None:
                f.text(f"{have}/{req}", sx + T / 2, 9 * T + 2, _COL_GREEN if have >= req else _COL_RED,
                       self._font_xs, anchor="midtop")
            if hov:
                self._slot_tooltip(mat_item)
        f.text("Resultado", T, 10.5 * T, _COL_GREY, self._font_sm, anchor="midleft")
        preview = self._frg_data["result_factory"]() if self._frg_data else None
        rx = T + f.tiles_for(["Resultado"], 1, self._font_sm) * T
        hov, _ = f.slot("bs:frg:result", rx, 10 * T, aw.item_icon(preview, self._font_md), dim=preview is not None)
        if hov:
            self._slot_tooltip(preview)
        if self._frg_pending:
            f.text("Forjando...", rx + T + 6, 10.5 * T, _COL_GREY, self._font_sm, anchor="midleft")
        cost = (RARITY_FORGE_COST.get(self._frg_data.get("result_rarity", "common"), 0)
                if self._frg_data else None)
        self._cost_line(f, 11, cost)
        wallet = self._wallet()
        can = (self._frg_data is not None and not self._frg_pending and wallet is not None
               and wallet.gold >= (cost or 0)
               and all(self._count_mat_in_bag(mid) >= q for mid, q in mat_list))
        if self._action_button(f, lay, "Forjar", can):
            self._do_forge()
        if picked is not None:
            self._frg_selected = picked
            self._frg_data = RECIPES.get(picked)

    def _draw_bag(self, f, lay, mode: str):
        T = aw.T
        inv = self._inv()
        items = inv.items if inv else []
        col0 = lay["bag_col0"]
        f.text(f"Mochila {len(items)}/{inv.max_slots if inv else 0}", col0 * T, 2.5 * T,
               (190, 170, 120), self._font_sm, anchor="midleft")
        cells = [None if it is None else
                 (aw.item_icon(it, self._font_md), it.stack if getattr(it, "max_stack", 1) > 1 else 0)
                 for it in items]
        rows = max(self._BAG_ROWS, math.ceil(len(items) / self._BAG_COLS))
        hov_i, _ = f.grid("bs:bag", col0, 3, self._BAG_COLS, rows, cells, count_font=self._font_xs)
        for i in range(len(items)):
            c, r = i % self._BAG_COLS, i // self._BAG_COLS
            self._hit(f"bs:bag:{i}", f, (col0 + c) * T, (3 + r) * T, (col0 + c + 1) * T, (4 + r) * T)
        if 0 <= hov_i < len(items) and items[hov_i] is not None:
            self._slot_tooltip(items[hov_i])
            if f.rreleased:
                self._bag_rclick(hov_i, mode, pygame.mouse.get_pos())

    # ── Menu de contexto "Deletar" ────────────────────────────────────
    def _draw_context_menu(self, events):
        T = aw.T
        inv = self._inv()
        idx = self._ctx_bag_idx
        if not inv or not 0 <= idx < len(inv.items):
            self._ctx_bag_idx = -1
            return
        item = inv.items[idx]
        bw = aw.tiles_for(self._font_sm, ["Deletar"], 4)
        tw = max(bw + 2, aw.tiles_for(self._font_sm, [item.name], 1) + 2)
        th = 4
        cx, cy = self._ctx_pos
        SW, SH = self.hud_surf.get_size()
        pw, ph = tw * T * aw.S, th * T * aw.S
        area = pygame.Rect(min(cx, SW - pw - 4), min(cy, SH - ph - 4), pw, ph)
        f = aw.Frame(self.hud_surf, events, tw, th, area=area, press=self._press, font=self._font_sm,
                     style="dark", overlay=False)
        f.text(item.name, tw * T / 2, T, _RARITY_COL.get(item.rarity, _COL_WHITE))
        delete = f.button("bs:ctx:del", (tw - bw) // 2, 2, bw, "Deletar", tall=False)
        ay0, ay1 = ui_atlas.button_art_y(False)
        self._hit("bs:ctx:del", f, (tw - bw) // 2 * T, 2 * T + ay0, ((tw - bw) // 2 + bw) * T, 2 * T + ay1)
        outside = f.clicked and not f.hover(0, 0, tw * T, th * T)
        f.end()
        if delete:
            self._do_delete_bag_item(idx)
            self._ctx_bag_idx = -1
        elif outside:
            self._ctx_bag_idx = -1
