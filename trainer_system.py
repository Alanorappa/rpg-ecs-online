# trainer_system.py
"""
TrainerSystem — gerencia interação com NPCs treinadores de classe.

Fluxo:
  1. Clique direito em NPC com componente Trainer → action menu
  2. Action menu: Treinamento / (quests, se NPC tiver QuestGiver)
  3. Modal de treinamento: lista de skills com requisito de nível e custo
"""
from __future__ import annotations
import pygame
from systems import System
from components import (Position, Renderable, TileMovement, PlayerAutoMove,
                        Camera, NPC, QuestGiver, Trainer, Wallet)
from skill_config import (SKILL_CATALOG, SKILL_LEVEL_REQUIREMENTS, SKILL_COSTS,
                          NUM_SLOTS, SKILL_ORDER_BY_CLASS)
from icon_manager import ICONS
from combat_log import LOG
from ui_scale_mixin import UIScaleMixin
from ui_sizes import UI

# ---------------------------------------------------------------------------
# Constantes visuais
# ---------------------------------------------------------------------------
_COL_BG      = (12,  10,   6)
_COL_PANEL   = (22,  18,  10)
_COL_BORDER  = (90,  70,  30)
_COL_TITLE   = (220, 180,  80)
_COL_WHITE   = (240, 220, 180)
_COL_GREY    = (110,  90,  60)
_COL_GOLD    = (220, 180,  50)
_COL_RED     = (200,  60,  60)
_COL_GREEN   = ( 60, 180,  60)
_COL_LEARNED = ( 60,  55,  40)
_COL_DARK    = ( 16,  13,   6)
_COL_BTN_OK  = ( 30,  90,  30)
_COL_BTN_DIS = ( 40,  35,  25)

_PANEL_W  = UI.TRAINER_W
_PANEL_H  = UI.TRAINER_H
_ROW_H    = UI.TRAINER_ROW_H
_ICON_SZ  = UI.TRAINER_ICON_SZ
_PAD      = UI.TRAINER_PAD

# Ordem padrão (guerreiro) — substituída dinamicamente por _skill_order_for()
_SKILL_ORDER = ["golpe_poderoso", "impacto", "vitoria_iminente", "interceptar", "executar"]


def _skill_order_for(class_id: str) -> list:
    return SKILL_ORDER_BY_CLASS.get(class_id, _SKILL_ORDER)


def _draw_text(surf, text: str, font, color, x: int, y: int, max_w: int = 0):
    """Renderiza texto, truncando com '...' se exceder max_w."""
    s = font.render(text, True, color)
    if max_w and s.get_width() > max_w:
        while len(text) > 1 and font.size(text + "...")[0] > max_w:
            text = text[:-1]
        s = font.render(text + "...", True, color)
    surf.blit(s, (x, y))
    return s.get_width()


class TrainerSystem(UIScaleMixin, System):
    """Gerencia o menu de ação e o modal de treinamento de skills."""

    STATE_CLOSED   = "CLOSED"
    STATE_MENU     = "ACTION_MENU"
    STATE_TRAINING = "TRAINING"

    _FONT_BASES = {"_font_sm": 20, "_font_md": 26, "_font_lg": 32, "_font_xl": 38}

    def __init__(self, world, player_entity: int, screen,
                 quest_dialog=None):
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self.hud_surf        = screen
        self._quest_dialog = quest_dialog

        SW, SH = screen.get_size()

        # Estado
        self._state:   str = self.STATE_CLOSED
        self._tr_eid:  int = -1
        self._tr_name: str = ""
        self._tr_class_id: str = "guerreiro"
        self._pending_tr_id: int = -1

        self._right_click_consumed: bool = False
        self._open_cooldown: float = 0.0

        # Rects armazenados no render para hit-test
        self._menu_rects:  dict = {}
        self._skill_rects: list = []   # [(skill_id, learn_btn_rect)]
        self._close_r: pygame.Rect | None = None
        self._list_scroll: int = 0

    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._state != self.STATE_CLOSED

    # ------------------------------------------------------------------
    # Helpers de navegação
    # ------------------------------------------------------------------
    def _player_tile(self):
        from components import TileMovement as _TM
        tm = self.world.get_component(self.player_entity, _TM)
        return (tm.current_tile_x, tm.current_tile_y) if tm else None

    def _tr_tile(self, eid: int):
        from tileset import TILE_SIZE as _TS
        tm = self.world.get_component(eid, TileMovement)
        if tm:
            return (tm.current_tile_x, tm.current_tile_y)
        pos = self.world.get_component(eid, Position)
        if pos:
            return (int(pos.x // _TS), int(pos.y // _TS))
        return None

    @staticmethod
    def _cheby(a, b) -> int:
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

    def _walk_to(self, eid: int):
        auto = self.world.get_component(self.player_entity, PlayerAutoMove)
        pt   = self._player_tile()
        bt   = self._tr_tile(eid)
        if auto and pt and bt:
            adj    = [(bt[0]+dx, bt[1]+dy) for dx, dy in ((-1,0),(1,0),(0,-1),(0,1))]
            target = min(adj, key=lambda t: abs(t[0]-pt[0]) + abs(t[1]-pt[1]))
            auto.ground_target     = target
            auto.path              = []
            auto.active            = True
            auto.path_recalc_timer = 0.0

    def _panel_origin(self):
        x0, y0 = self._safe_panel_origin(_PANEL_W, _PANEL_H)
        return x0 + UI.TRAINER_OFFSET_X, y0 + UI.TRAINER_OFFSET_Y

    def _player_level(self) -> int:
        from components import CharacterStats
        cs = self.world.get_component(self.player_entity, CharacterStats)
        return cs.level if cs else 1

    def _player_skills(self):
        from components import PlayerSkills
        return self.world.get_component(self.player_entity, PlayerSkills)

    def _player_wallet(self):
        return self.world.get_component(self.player_entity, Wallet)

    # ------------------------------------------------------------------
    # update() — detecção de clique + pendência de chegada
    # ------------------------------------------------------------------
    def update(self, events=None, dt: float = 0):
        if events is None:
            events = []

        self._right_click_consumed = False

        if self._open_cooldown > 0:
            self._open_cooldown -= dt

        # Chegou ao treinador pendente?
        if self._pending_tr_id != -1:
            pt = self._player_tile()
            bt = self._tr_tile(self._pending_tr_id)
            if pt and bt and self._cheby(pt, bt) <= 1:
                self._open_menu(self._pending_tr_id)
                self._pending_tr_id = -1

        # ESC
        for ev in events:
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                if self._state != self.STATE_CLOSED:
                    self._close()
                return

        # Clique direito em Trainer (apenas quando fechado)
        if self._state == self.STATE_CLOSED and self._open_cooldown <= 0:
            SW, SH = self.hud_surf.get_size()
            cx, cy = 0.0, 0.0
            for _, _, _cam_pos in self.world.get_entities_with(Camera, Position):
                cx = _cam_pos.x - SW / 2
                cy = _cam_pos.y - SH / 2
                break

            for eid, pos, rend, _ in self.world.get_entities_with(
                    Position, Renderable, Trainer):
                for ev in events:
                    if ev.type != pygame.MOUSEBUTTONDOWN or ev.button != 3:
                        continue
                    wx = ev.pos[0] + cx
                    wy = ev.pos[1] + cy
                    hw = rend.width  / 2
                    hh = rend.height / 2
                    if abs(wx - pos.x) <= hw and abs(wy - pos.y) <= hh:
                        pt = self._player_tile()
                        bt = self._tr_tile(eid)
                        if pt and bt and self._cheby(pt, bt) <= 1:
                            self._open_menu(eid)
                        else:
                            self._pending_tr_id = eid
                            self._walk_to(eid)
                        self._right_click_consumed = True
                        break
                if self._right_click_consumed:
                    break

    # ------------------------------------------------------------------
    # handle_events() — quando is_open
    # ------------------------------------------------------------------
    def handle_events(self, events):
        for ev in events:
            if ev.type == pygame.MOUSEWHEEL and self._state == self.STATE_TRAINING:
                self._set_panel_scale(_PANEL_W, _PANEL_H)
                n = len(_skill_order_for(self._tr_class_id))
                visible_rows = (self._u(_PANEL_H) - self._u(60) - self._u(_PAD)) // self._u(_ROW_H)
                max_scroll   = max(0, n - visible_rows)
                self._list_scroll = max(0, min(max_scroll, self._list_scroll - ev.y))
                continue

            if ev.type != pygame.MOUSEBUTTONDOWN:
                continue
            mx, my = ev.pos

            # Botão fechar
            if ev.button == 1 and self._close_r and self._close_r.collidepoint(mx, my):
                self._close()
                return

            # Action menu
            if self._state == self.STATE_MENU and ev.button == 1:
                for key, rect in self._menu_rects.items():
                    if not rect.collidepoint(mx, my):
                        continue
                    if key == "treinamento":
                        self._state = self.STATE_TRAINING
                        self._list_scroll = 0
                    elif key.startswith("quest:"):
                        if self._quest_dialog:
                            self._quest_dialog._open_dialog(self._tr_eid)
                        self._state = self.STATE_CLOSED
                    elif key == "_none":
                        self._close()
                    return

            # Training modal — botões "Aprender"
            if self._state == self.STATE_TRAINING and ev.button == 1:
                for skill_id, btn_r in self._skill_rects:
                    if btn_r and btn_r.collidepoint(mx, my):
                        self._do_learn(skill_id)
                        return

    # ------------------------------------------------------------------
    # Ações internas
    # ------------------------------------------------------------------
    def _open_menu(self, eid: int):
        self._tr_eid  = eid
        npc = self.world.get_component(eid, NPC)
        self._tr_name = npc.name if npc else "Treinador"
        tr  = self.world.get_component(eid, Trainer)
        self._tr_class_id = tr.class_id if tr else "guerreiro"
        self._state = self.STATE_MENU
        self._open_cooldown = 0.3

    def _close(self):
        self._state         = self.STATE_CLOSED
        self._tr_eid        = -1
        self._pending_tr_id = -1
        self._open_cooldown = 0.2

    def _do_learn(self, skill_id: str):
        ps     = self._player_skills()
        wallet = self._player_wallet()
        if not ps or not wallet:
            return

        if skill_id in ps.learned_skill_ids:
            return

        req   = SKILL_LEVEL_REQUIREMENTS.get(skill_id, 99)
        cost  = SKILL_COSTS.get(skill_id, 9999)
        level = self._player_level()

        if level < req:
            LOG.add(f"Requer nível {req} para aprender.", _COL_RED)
            return
        if wallet.gold < cost:
            LOG.add(f"Ouro insuficiente. Necessário: {cost}g", _COL_RED)
            return

        from components import CharacterStats
        cs = self.world.get_component(self.player_entity, CharacterStats)
        if cs and cs.class_id != self._tr_class_id:
            LOG.add(
                f"Apenas {self._tr_class_id.capitalize()}s podem aprender essas habilidades.",
                _COL_RED,
            )
            return

        # Deduz custo e registra skill como aprendida
        wallet.gold -= cost
        ps.learned_skill_ids.add(skill_id)

        # Cria objeto Skill e insere no primeiro slot vazio da hotbar
        new_skill = ps._make_skill(skill_id, SKILL_CATALOG)
        if new_skill:
            try:
                idx = ps.skills.index(None)
                ps.skills[idx] = new_skill
            except ValueError:
                ps.skills.append(new_skill)

        entry = SKILL_CATALOG.get(skill_id, {})
        name  = entry.get("name", skill_id) if isinstance(entry, dict) else skill_id
        LOG.add(f"Aprendido: {name} (-{cost}g)", _COL_GOLD)

        from save_system import request_autosave
        request_autosave()

    # ------------------------------------------------------------------
    # render_world() — indicador sobre NPC
    # ------------------------------------------------------------------
    def render_world(self, cam_x: float, cam_y: float):
        from components import Visible as _Vis
        font = self._font_sm
        for eid, pos, rend, _ in self.world.get_entities_with(
                Position, Renderable, Trainer):
            if not self.world.get_component(eid, _Vis):
                continue
            sx = int(pos.x - cam_x) - rend.width  // 2
            sy = int(pos.y - cam_y) - rend.height // 2
            label = font.render("T", True, (120, 200, 255))
            self.world_surf.blit(label, (sx + rend.width // 2 - label.get_width() // 2,
                                     sy - 14))

    # ------------------------------------------------------------------
    # render() — modal de UI
    # ------------------------------------------------------------------
    def render(self):
        if self._state == self.STATE_CLOSED:
            return
        if self._state == self.STATE_MENU:
            self._draw_action_menu()
        elif self._state == self.STATE_TRAINING:
            self._draw_training_modal()

    # ------------------------------------------------------------------
    # Action menu (igual ao BlacksmithSystem)
    # ------------------------------------------------------------------
    def _draw_action_menu(self):
        surf = self.hud_surf
        mx, my = pygame.mouse.get_pos()
        SW, SH = surf.get_size()

        # Opções disponíveis
        options = []
        from components import CharacterStats
        cs = self.world.get_component(self.player_entity, CharacterStats)
        if cs and cs.class_id == self._tr_class_id:
            options.append(("treinamento", "Treinamento"))
        qg = self.world.get_component(self._tr_eid, QuestGiver)
        if qg and qg.quest_ids:
            options.append(("quest:0", "Quests"))
        if not options:
            options.append(("_none", f"Nada para {cs.name if cs else 'você'} aqui."))

        BTN_W, BTN_H = self._u(220), self._u(46)
        GAP = self._u(8)
        total_h = len(options) * (BTN_H + GAP) - GAP + self._u(60)
        px = (SW - BTN_W) // 2
        py = (SH - total_h) // 2

        # Fundo
        pad = self._u(_PAD)
        bg = pygame.Rect(px - pad, py - pad, BTN_W + pad * 2, total_h + pad * 2)
        pygame.draw.rect(surf, _COL_PANEL, bg, border_radius=6)
        pygame.draw.rect(surf, _COL_BORDER, bg, 2, border_radius=6)

        # Título
        title_s = self._font_lg.render(self._tr_name, True, _COL_TITLE)
        surf.blit(title_s, (px + (BTN_W - title_s.get_width()) // 2, py))
        py += self._u(46)

        self._menu_rects = {}
        for key, label in options:
            r = pygame.Rect(px, py, BTN_W, BTN_H)
            hov = r.collidepoint(mx, my)
            pygame.draw.rect(surf, (40, 35, 20) if hov else _COL_DARK, r, border_radius=4)
            pygame.draw.rect(surf, _COL_BORDER if hov else (60, 50, 25), r, 1, border_radius=4)
            s = self._font_md.render(label, True, _COL_TITLE if hov else _COL_WHITE)
            surf.blit(s, (r.x + (r.w - s.get_width()) // 2, r.y + (r.h - s.get_height()) // 2))
            self._menu_rects[key] = r
            py += BTN_H + GAP

    # ------------------------------------------------------------------
    # Training modal
    # ------------------------------------------------------------------
    def _draw_training_modal(self):
        surf = self.hud_surf
        mx, my = pygame.mouse.get_pos()
        x0, y0 = self._panel_origin()

        panel_w, panel_h = self._u(_PANEL_W), self._u(_PANEL_H)
        pad     = self._u(_PAD)
        icon_sz = self._u(_ICON_SZ)
        row_h   = self._u(_ROW_H)

        # Fundo do painel
        panel_r = pygame.Rect(x0, y0, panel_w, panel_h)
        pygame.draw.rect(surf, _COL_BG, panel_r)
        pygame.draw.rect(surf, _COL_BORDER, panel_r, 2, border_radius=4)

        # Barra de título
        title_bar = pygame.Rect(x0, y0, panel_w, self._u(40))
        pygame.draw.rect(surf, _COL_PANEL, title_bar)
        pygame.draw.line(surf, _COL_BORDER, (x0, y0 + self._u(40)), (x0 + panel_w, y0 + self._u(40)))
        title_s = self._font_lg.render(
            f"{self._tr_name}  —  Treinamento", True, _COL_TITLE)
        surf.blit(title_s, (x0 + pad, y0 + self._u(10)))

        # Botão fechar
        close_s = self._font_md.render("[X]", True, _COL_RED)
        self._close_r = pygame.Rect(x0 + panel_w - self._u(36), y0 + self._u(8), self._u(28), self._u(24))
        surf.blit(close_s, self._close_r.topleft)

        # Cabeçalho de colunas
        hy = y0 + self._u(46)
        pygame.draw.line(surf, _COL_BORDER, (x0, hy + self._u(18)), (x0 + panel_w, hy + self._u(18)))
        surf.blit(self._font_sm.render("Habilidade", True, _COL_GREY),  (x0 + pad + icon_sz + self._u(8), hy))
        surf.blit(self._font_sm.render("Nível",      True, _COL_GREY),  (x0 + self._u(390), hy))
        surf.blit(self._font_sm.render("Custo",      True, _COL_GREY),  (x0 + self._u(470), hy))

        # Lista de skills
        ps    = self._player_skills()
        level = self._player_level()
        list_y0 = y0 + self._u(66)
        visible_rows = (panel_h - self._u(66) - pad) // row_h

        skill_order = _skill_order_for(self._tr_class_id)
        self._skill_rects = []
        for i, skill_id in enumerate(skill_order):
            vi = i - self._list_scroll
            if vi < 0 or vi >= visible_rows:
                self._skill_rects.append((skill_id, None))
                continue

            entry     = SKILL_CATALOG.get(skill_id, {})
            name      = entry.get("name", skill_id) if isinstance(entry, dict) else skill_id
            desc      = entry.get("desc", "")        if isinstance(entry, dict) else ""
            req       = SKILL_LEVEL_REQUIREMENTS.get(skill_id, 99)
            cost      = SKILL_COSTS.get(skill_id, 0)
            learned   = ps is not None and skill_id in ps.learned_skill_ids
            can_learn = (not learned) and level >= req and (
                ps is None or self._player_wallet() is not None and
                self._player_wallet().gold >= cost)

            ry = list_y0 + vi * row_h
            row_r = pygame.Rect(x0 + self._u(2), ry, panel_w - self._u(4), row_h - self._u(2))

            # Fundo alternado
            bg_col = _COL_LEARNED if learned else (_COL_PANEL if i % 2 == 0 else _COL_DARK)
            pygame.draw.rect(surf, bg_col, row_r, border_radius=3)

            # Ícone da skill
            icon_r    = pygame.Rect(x0 + pad, ry + (row_h - icon_sz) // 2, icon_sz, icon_sz)
            icon_surf = ICONS.get(f"skill_{skill_id}", icon_sz)
            if icon_surf:
                surf.blit(icon_surf, icon_r)
                if learned:   # overlay escurecido quando já aprendida
                    dim = pygame.Surface((icon_sz, icon_sz), pygame.SRCALPHA)
                    dim.fill((0, 0, 0, 120))
                    surf.blit(dim, icon_r)
            else:
                icon_col = (80, 80, 80) if learned else ((50, 100, 50) if can_learn else (80, 30, 30))
                pygame.draw.rect(surf, icon_col, icon_r, border_radius=3)
                init_s = self._font_md.render(name[0].upper(), True,
                                              _COL_GREY if learned else _COL_WHITE)
                surf.blit(init_s, (icon_r.centerx - init_s.get_width() // 2,
                                   icon_r.centery - init_s.get_height() // 2))
            pygame.draw.rect(surf, _COL_BORDER, icon_r, 1, border_radius=3)

            # Nome + descrição
            tx = x0 + pad + icon_sz + self._u(10)
            name_col = _COL_GREY if learned else _COL_WHITE
            surf.blit(self._font_md.render(name, True, name_col), (tx, ry + self._u(10)))
            surf.blit(self._font_sm.render(desc, True, _COL_GREY),  (tx, ry + self._u(32)))

            # Nível requerido
            req_col = _COL_GREEN if level >= req else _COL_RED
            surf.blit(self._font_md.render(f"Nível {req}", True, req_col), (x0 + self._u(385), ry + self._u(22)))

            # Custo
            surf.blit(self._font_md.render(f"{cost}g", True, _COL_GOLD), (x0 + self._u(468), ry + self._u(22)))

            # Botão Aprender / Aprendido
            btn_w, btn_h = self._u(90), self._u(30)
            btn_r = pygame.Rect(x0 + panel_w - btn_w - pad,
                                ry + (row_h - btn_h) // 2, btn_w, btn_h)

            if learned:
                pygame.draw.rect(surf, _COL_BTN_DIS, btn_r, border_radius=4)
                s = self._font_sm.render("Aprendido", True, _COL_GREY)
                surf.blit(s, (btn_r.centerx - s.get_width() // 2,
                              btn_r.centery - s.get_height() // 2))
                self._skill_rects.append((skill_id, None))
            else:
                btn_enabled = level >= req and (
                    self._player_wallet() and self._player_wallet().gold >= cost)
                btn_col = _COL_BTN_OK if (btn_enabled and btn_r.collidepoint(mx, my)) else \
                          (40, 70, 40) if btn_enabled else _COL_BTN_DIS
                pygame.draw.rect(surf, btn_col, btn_r, border_radius=4)
                pygame.draw.rect(surf, _COL_GREEN if btn_enabled else _COL_BORDER,
                                 btn_r, 1, border_radius=4)
                s = self._font_sm.render("Aprender", True,
                                         _COL_WHITE if btn_enabled else _COL_GREY)
                surf.blit(s, (btn_r.centerx - s.get_width() // 2,
                              btn_r.centery - s.get_height() // 2))
                self._skill_rects.append((skill_id, btn_r if btn_enabled else None))

            # Separador de linha
            pygame.draw.line(surf, _COL_BORDER,
                             (x0 + self._u(2), ry + row_h - self._u(2)), (x0 + panel_w - self._u(2), ry + row_h - self._u(2)))

        # Scrollbar simples (se necessário)
        n = len(_SKILL_ORDER)
        if n > visible_rows:
            sb_h   = panel_h - self._u(66) - pad
            thumb_h = max(self._u(20), int(sb_h * visible_rows / n))
            thumb_y = list_y0 + int((sb_h - thumb_h) * self._list_scroll / max(1, n - visible_rows))
            pygame.draw.rect(surf, _COL_BORDER,
                             pygame.Rect(x0 + panel_w - self._u(6), list_y0, self._u(4), sb_h))
            pygame.draw.rect(surf, _COL_TITLE,
                             pygame.Rect(x0 + panel_w - self._u(6), thumb_y, self._u(4), thumb_h))

        # Ouro do jogador (footer)
        wallet = self._player_wallet()
        if wallet:
            gold_s = self._font_md.render(f"Seu ouro: {wallet.gold}g", True, _COL_GOLD)
            surf.blit(gold_s, (x0 + pad, y0 + panel_h - self._u(26)))

        # Nível do jogador (footer direita)
        lv_s = self._font_md.render(f"Seu nível: {level}", True, _COL_WHITE)
        surf.blit(lv_s, (x0 + panel_w - lv_s.get_width() - pad, y0 + panel_h - self._u(26)))
