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
from ui.npc_interaction import (walk_to_npc, suppress_chase, service_tabs, open_npc_service,
                                default_npc_service)


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
    """Janela Reciclar/Forjar do ferreiro (abas de serviço do NPC —
    ui/npc_interaction.py; Negociar é a loja, Missões o diálogo de quest)."""

    # Estado da máquina de estados
    STATE_CLOSED  = "CLOSED"
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
        # Injetado por game.py: avisa o servidor de mudança LOCAL na mochila
        # (INV_SYNC + save) — mesmo caminho da tecla Delete do inventário.
        self.on_inventory_changed = None
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

        # Aba Reciclar/Forjar pedida por outra janela de NPC (abas de
        # serviço, ui/npc_interaction.py) — só consome os kinds DESTE System.
        _npc_req = self.world.get_component(self.player_entity, NPCInteractionRequest)
        if _npc_req is not None and _npc_req.kind in ("open_recycle", "open_forge"):
            self.world.remove_component(self.player_entity, NPCInteractionRequest)
            self._open_window(_npc_req.target_eid,
                              "recycle" if _npc_req.kind == "open_recycle" else "forge")

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
        # Abre direto a função padrão do NPC (Missões se houver quest pra
        # aceitar/entregar; senão Negociar) — sem menu intermediário
        # (21/10/2026, pedido do usuário; ui/npc_interaction.py).
        key = default_npc_service(self.world, self.player_entity, eid)
        if key in ("recycle", "forge"):
            self._open_window(eid, key)
            return
        if key is None:
            # Travado por quest e nada a oferecer agora: o diálogo de quest
            # mostra a mensagem de sempre ("continue sua missão"...).
            key = "quest"
        open_npc_service(self.world, self.player_entity, eid, key)
        self._open_cooldown = 0.3

    def _open_window(self, eid: int, key: str) -> None:
        """Abre a janela Reciclar/Forjar deste ferreiro na aba `key`."""
        self._bs_eid  = eid
        npc = self.world.get_component(eid, NPC)
        self._bs_name = npc.name if npc else "Ferreiro"
        self._state   = self.STATE_RECYCLE if key == "recycle" else self.STATE_FORGE
        self._reset_recycle()
        self._reset_forge()
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
        # Mutação local de Inventory precisa virar real pro servidor
        # (CLAUDE.md, "mutação local de Inventory"; HISTORICO_BUGS.md §96) —
        # mesmo caminho da tecla Delete do inventário.
        inv = self._inv()
        if inv and 0 <= idx < len(inv.items):
            name = inv.items[idx].name
            inv.items.pop(idx)
            LOG.add(f"Item deletado: {name}", _COL_GREY)
            if self.on_inventory_changed is not None:
                self.on_inventory_changed()

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
    _LIST_ROWS = 3          # receitas visíveis antes de rolar
    _SLOT_STEP = aw.SLOT + 2   # materiais: slot de ícone 32x32 + folga

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
        ctx = self._ctx_bag_idx >= 0
        self._draw_craft_window([] if ctx else events, ctx)
        if ctx:
            self._draw_context_menu(events)

    # ── Janela Reciclar/Forjar ────────────────────────────────────────
    def _craft_layout(self) -> dict:
        T, S = aw.T, aw.S
        from engine.components import LearnedRecipes
        lr = self.world.get_component(self.player_entity, LearnedRecipes)
        known = [r for r in (lr.known if lr else []) if r in RECIPES]
        names = [RECIPES[r]["name"] for r in known] + ["Nenhuma receita aprendida.",
                                                       "Receitas conhecidas", "Custo: 9999g"]
        text_w = max(self._font_sm.size(n)[0] for n in names) / S
        # Painel Reciclar/Forjar à esquerda (barra de rolagem das receitas
        # colada à direita dele), mochila à direita — mesmo lado da loja
        # (aba Negociar), pra mochila não "pular" ao trocar de aba.
        panel_x0 = 8
        panel_w = max(text_w + 16, 5 * self._SLOT_STEP + 4)
        sb_col = math.ceil((panel_x0 + panel_w) / T)
        div_x = (sb_col + 1) * T + 4
        bag_x0 = div_x + 8
        tw = math.ceil((bag_x0 + aw.grid_px(self._BAG_COLS) + 8) / T)
        th = 15
        return {"tw": tw, "th": th, "bag_x0": bag_x0, "div_x": div_x, "px": panel_x0,
                "pw": sb_col * T - panel_x0, "sb_col": sb_col, "known": known}

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
        # Abas das funções do NPC (Missões/Negociar/Reciclar/Forjar —
        # ui/npc_interaction.py::service_tabs). Reciclar↔Forjar troca aqui
        # mesmo; as outras fecham esta janela e pedem a do System dono.
        tab = None if ctx_open else service_tabs(
            f, self.world, self.player_entity, self._bs_eid, "recycle" if recycle else "forge",
            self._font_sm, self._hit_rects)
        f.divider_v(lay["div_x"], 2 * T, (th - 1) * T)

        if recycle:
            self._draw_recycle_left(f, lay)
        else:
            self._draw_forge_left(f, lay)
        self._draw_bag(f, lay, "recycle" if recycle else "forge")
        f.end()
        if self._post_draw is not None:
            self._post_draw()
            self._post_draw = None

        if tab == "recycle":
            self._state = self.STATE_RECYCLE
            self._reset_recycle()
        elif tab == "forge":
            self._state = self.STATE_FORGE
            self._reset_forge()
        elif tab is not None:
            npc_eid = self._bs_eid
            self._close()
            open_npc_service(self.world, self.player_entity, npc_eid, tab)

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

    def _cost_line(self, f, x: float, y: float, cost) -> None:
        if cost is None:
            f.text("Custo: —", x, y, _COL_GREY, self._font_sm, anchor="midleft")
            return
        wallet = self._wallet()
        ok = wallet is not None and wallet.gold >= cost
        f.text(f"Custo: {cost}g", x, y, _COL_WHITE if ok else _COL_RED,
               self._font_sm, anchor="midleft")

    def _action_button(self, f, lay, label: str, enabled: bool) -> bool:
        T = aw.T
        bw = f.tiles_for([label], 4)
        col = (lay["px"] + (lay["pw"] - bw * T) / 2) / T     # centralizado no painel
        fired = f.button(f"bs:{label}", col, lay["th"] - 2, bw, label, enabled=enabled)
        ay0, ay1 = ui_atlas.button_art_y(True)
        self._hit(f"bs:{label}", f, col * T, (lay["th"] - 2) * T + ay0, (col + bw) * T, (lay["th"] - 2) * T + ay1)
        return fired

    def _draw_recycle_left(self, f, lay):
        px, SL = lay["px"], aw.SLOT
        f.text("Item", px, 40, _COL_GREY, self._font_sm, anchor="midleft")
        hov, _ = f.slot("bs:rec:item", px, 48, aw.item_icon(self._rec_item, self._font_md))
        if hov:
            self._slot_tooltip(self._rec_item)
        mats = get_recycle_materials(self._rec_item) if self._rec_item else []
        if self._rec_item is not None:
            if not mats:
                f.text("Não reciclável", px + SL + 6, 48 + SL / 2, _COL_RED, self._font_sm, anchor="midleft")
            else:
                f.text(self._rec_item.name, px + SL + 6, 48 + SL / 2,
                       _RARITY_COL.get(self._rec_item.rarity, _COL_WHITE), self._font_sm, anchor="midleft")
        f.text("Materiais", px, 94, _COL_GREY, self._font_sm, anchor="midleft")
        for i in range(5):
            preview = None
            if i < len(mats):
                mat_id, qty = mats[i]
                preview = MATERIALS[mat_id]() if mat_id in MATERIALS else None
                if preview:
                    preview.stack = qty
            hov, _ = f.slot(f"bs:rec:mat:{i}", px + i * self._SLOT_STEP, 102, aw.item_icon(preview, self._font_md),
                            preview.stack if preview and preview.stack > 1 else 0, dark=True,
                            count_font=self._font_xs)
            if hov:
                self._slot_tooltip(preview)
        self._cost_line(f, px, 152, RARITY_RECYCLE_COST.get(self._rec_item.rarity, 0) if self._rec_item else None)
        wallet = self._wallet()
        can = (self._rec_item is not None and not self._rec_pending and bool(mats)
               and wallet is not None and wallet.gold >= RARITY_RECYCLE_COST.get(self._rec_item.rarity, 0))
        if self._action_button(f, lay, "Reciclar", can):
            self._do_recycle()

    def _draw_forge_left(self, f, lay):
        T, S = aw.T, aw.S
        known = lay["known"]
        px, SL = lay["px"], aw.SLOT
        f.text("Receitas conhecidas", px, 40, _COL_GREY, self._font_sm, anchor="midleft")
        lw = lay["pw"]                     # até a barra de rolagem (última coluna)
        view_y0, view_h = 3 * T, self._LIST_ROWS * T
        content_h = max(1, len(known) * T)
        off = f.scrollbar(self._frg_scroll, lay["sb_col"], 3, 3 + self._LIST_ROWS - 1,
                          max(0, content_h - view_h))
        in_view = view_y0 <= f.ry < view_y0 + view_h and px <= f.rx < px + lw
        fc = f.child(pygame.Surface((lw, content_h), pygame.SRCALPHA),
                     f.ry - view_y0 + off if in_view else -10_000)
        fc.rx = f.rx - px
        if not known:
            f.text("Nenhuma receita aprendida.", px, 3.5 * T, _COL_GREY, self._font_sm, anchor="midleft")
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
                self._hit(f"bs:recipe:{i}", f, px, ry, px + lw, ry + T)
        if known:
            def content():
                surf = ui_atlas.scale_up(fc.raw)
                aw.draw_texts(surf, fc.texts, self._font_sm)
                view = surf.subsurface((0, off * S, surf.get_width(), max(0, min(view_h, content_h - off)) * S))
                self.hud_surf.blit(view, (f.px + px * S, f.py + view_y0 * S))
            self._post_draw = content   # desenhado depois do painel (f.end)

        mat_list = self._frg_data["materials"] if self._frg_data else []
        f.text("Materiais", px, 104, _COL_GREY, self._font_sm, anchor="midleft")
        for i in range(5):
            sx = px + i * self._SLOT_STEP
            mat_item, have, req = None, 0, 0
            if i < len(mat_list):
                mat_id, req = mat_list[i]
                mat_item = MATERIALS.get(mat_id, lambda: None)()
                have = self._count_mat_in_bag(mat_id) if mat_item else 0
                if mat_item:
                    mat_item.stack = req
            hov, _ = f.slot(f"bs:frg:mat:{i}", sx, 112, aw.item_icon(mat_item, self._font_md),
                            dark=True, dim=mat_item is not None and have < req)
            if mat_item is not None:
                f.text(f"{have}/{req}", sx + SL / 2, 112 + SL + 1, _COL_GREEN if have >= req else _COL_RED,
                       self._font_xs, anchor="midtop")
            if hov:
                self._slot_tooltip(mat_item)
        ry0 = 164
        f.text("Resultado", px, ry0 + SL / 2, _COL_GREY, self._font_sm, anchor="midleft")
        preview = self._frg_data["result_factory"]() if self._frg_data else None
        rx = px + f.tiles_for(["Resultado"], 1, self._font_sm) * T
        hov, _ = f.slot("bs:frg:result", rx, ry0, aw.item_icon(preview, self._font_md), dim=preview is not None)
        if hov:
            self._slot_tooltip(preview)
        cost = (RARITY_FORGE_COST.get(self._frg_data.get("result_rarity", "common"), 0)
                if self._frg_data else None)
        if self._frg_pending:
            f.text("Forjando...", rx + SL + 8, ry0 + SL / 2, _COL_GREY, self._font_sm, anchor="midleft")
        else:
            self._cost_line(f, rx + SL + 8, ry0 + SL / 2, cost)
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
        x0, y0, SL = lay["bag_x0"], 3 * T, aw.SLOT
        f.text(f"Mochila {len(items)}/{inv.max_slots if inv else 0}", x0, 2.5 * T,
               (190, 170, 120), self._font_sm, anchor="midleft")
        cells = [None if it is None else
                 (aw.item_icon(it, self._font_md), it.stack if getattr(it, "max_stack", 1) > 1 else 0)
                 for it in items]
        rows = max(self._BAG_ROWS, math.ceil(len(items) / self._BAG_COLS))
        hov_i, _ = f.grid("bs:bag", x0, y0, self._BAG_COLS, rows, cells, count_font=self._font_xs)
        for i in range(len(items)):
            c, r = i % self._BAG_COLS, i // self._BAG_COLS
            x, y = x0 + c * SL, y0 + r * SL
            self._hit(f"bs:bag:{i}", f, x, y, x + SL, y + SL)
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
