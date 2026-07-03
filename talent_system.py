"""
Sistema de Árvore de Talentos
==============================
Gerencia UI e lógica de alocação da árvore de talentos do Cavaleiro.
"""

from __future__ import annotations
import pygame
from fonts import make as _font
from talent_data import BUILDS, TALENTS, BUILD_TALENTS
from combat_log import LOG
from save_system import request_autosave
from stat_fns import add_modifier, remove_modifier
from ui_scale_mixin import UIScaleMixin
from ui_sizes import UI
from ui_helpers import fill_surf


# ---------------------------------------------------------------------------
# Constantes de UI — valores em ui_sizes.py (UI.TALENTS_*)
# ---------------------------------------------------------------------------
PANEL_W       = UI.TALENTS_W
PANEL_H       = UI.TALENTS_H
NODE_W        = UI.TALENTS_NODE_W
NODE_H        = UI.TALENTS_NODE_H
NODE_COL_GAP  = UI.TALENTS_NODE_COL_GAP    # distância centro-a-centro horizontal
NODE_ROW_GAP  = UI.TALENTS_NODE_ROW_GAP    # distância centro-a-centro vertical
GRID_ORIGIN_X = UI.TALENTS_GRID_ORIGIN_X   # margem esquerda dentro do painel
GRID_ORIGIN_Y = UI.TALENTS_GRID_ORIGIN_Y   # margem superior dentro do painel

TOOLTIP_W     = UI.TALENTS_TOOLTIP_W   # largura do tooltip flutuante

# Cores
C_BG         = (14, 10, 6, 230)
C_BORDER     = (100, 80, 50)
C_TITLE      = (220, 190, 120)
C_WHITE      = (230, 230, 230)
C_GRAY       = (140, 130, 110)
C_GOLD       = (255, 210, 60)
C_GREEN      = (80, 200, 100)
C_RED        = (220, 80, 80)
C_LOCKED     = (30, 22, 12)
C_LOCKED_BDR = (55, 44, 30)
C_MAXED      = (255, 200, 50)
C_CONNECT    = (70, 56, 35)
C_CONNECT_OK = (100, 170, 70)
C_LOCKED_TXT = (75, 65, 48)


class TalentSystem(UIScaleMixin):
    """Gerencia a UI e a lógica de alocação da árvore de talentos."""

    _FONT_BASES = {
        "font_lg": 26,
        "font_md": 20,
        "font_sm": 17,
        "font_tip_title": 28,
        "font_tip_body": 22,
    }

    def __init__(self, world, player_entity_id: int, screen: pygame.Surface):
        super().__init__()
        self.world       = world
        self.player_id   = player_entity_id
        self.screen      = screen
        # font_lg: cabeçalho do painel | font_md: subheader e textos de nó
        # font_sm: contador/estrela nos nós (espaço pequeno)
        # font_tip_title/font_tip_body: tooltip — mesma escala do tooltip de itens
        # (todas criadas/escaladas por UIScaleMixin.set_ui_scale via _FONT_BASES)
        self._hovered_id: str | None = None
        self.wants_close: bool = False
        self._on_change: "callable | None" = None

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    def _talent_tree(self):
        from components import TalentTree
        return self.world.get_component(self.player_id, TalentTree)

    def _panel_rect(self) -> pygame.Rect:
        x0, y0 = self._safe_panel_origin(PANEL_W, PANEL_H)
        x0, y0 = x0 + UI.TALENTS_OFFSET_X, y0 + UI.TALENTS_OFFSET_Y
        pw, ph = self._u(PANEL_W), self._u(PANEL_H)
        return pygame.Rect(x0, y0, pw, ph)

    def _node_rect(self, panel: pygame.Rect, talent_id: str) -> pygame.Rect:
        t = TALENTS[talent_id]
        x = panel.x + self._u(GRID_ORIGIN_X) + t["col"] * self._u(NODE_COL_GAP)
        y = panel.y + self._u(GRID_ORIGIN_Y) + t["row"] * self._u(NODE_ROW_GAP)
        return pygame.Rect(x, y, self._u(NODE_W), self._u(NODE_H))

    def _close_btn_rect(self, panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.right - self._u(34), panel.y + self._u(4), self._u(30), self._u(30))

    def _reset_btn_rect(self, panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.right - self._u(130), panel.y + self._u(8), self._u(90), self._u(22))

    # -----------------------------------------------------------------------
    # Lógica de alocação
    # -----------------------------------------------------------------------
    def can_allocate(self, talent_id: str, tt) -> bool:
        if tt.available_points <= 0:
            return False
        t = TALENTS.get(talent_id)
        if t is None:
            return False
        current = tt.allocated.get(talent_id, 0)
        if current >= t["max_points"]:
            return False
        for req_id, req_pts in t["requires"].items():
            if tt.allocated.get(req_id, 0) < req_pts:
                return False
        return True

    def can_deallocate(self, talent_id: str, tt) -> bool:
        current = tt.allocated.get(talent_id, 0)
        if current <= 0:
            return False
        new_level = current - 1
        for tid, t in TALENTS.items():
            if tt.allocated.get(tid, 0) <= 0:
                continue
            if t["requires"].get(talent_id, 0) > new_level:
                return False
        return True

    def allocate(self, talent_id: str):
        tt = self._talent_tree()
        if not tt or not self.can_allocate(talent_id, tt):
            return
        tt.allocated[talent_id] = tt.allocated.get(talent_id, 0) + 1
        tt.available_points -= 1
        self.apply_talent_effects()
        t = TALENTS[talent_id]
        LOG.add(f"Talento: {t['name']} ({tt.allocated[talent_id]}/{t['max_points']})", C_GOLD)
        request_autosave()
        if self._on_change:
            self._on_change()

    def deallocate(self, talent_id: str):
        tt = self._talent_tree()
        if not tt or not self.can_deallocate(talent_id, tt):
            return
        tt.allocated[talent_id] -= 1
        tt.available_points += 1
        self.apply_talent_effects()
        request_autosave()
        if self._on_change:
            self._on_change()

    def reset_talents(self):
        tt = self._talent_tree()
        if not tt:
            return
        total = sum(tt.allocated.values())
        tt.available_points += total
        tt.allocated.clear()
        self.apply_talent_effects()
        if total:
            LOG.add(f"{total} pontos de talento reembolsados.", C_GOLD)
        request_autosave()
        if self._on_change:
            self._on_change()

    # -----------------------------------------------------------------------
    # Aplicação de efeitos
    # -----------------------------------------------------------------------
    def apply_talent_effects(self):
        from components import TalentTree, CombatStats, PlayerSkills, Modifier, CharacterStats
        from talent_data import CLASS_BUILD_MAP
        tt     = self._talent_tree()
        cs     = self.world.get_component(self.player_id, CombatStats)
        skills = self.world.get_component(self.player_id, PlayerSkills)
        if not tt or not cs or not skills:
            return

        # Build é sempre derivada da classe — nunca livre nem persistida separadamente
        char = self.world.get_component(self.player_id, CharacterStats)
        if char:
            correct_build = CLASS_BUILD_MAP.get(char.class_id, "cavaleiro")
            if tt.chosen_build != correct_build:
                # Reembolsa os pontos ANTES de limpar — bug real: isso fazia só
                # tt.allocated.clear() sem devolver nada a available_points,
                # destruindo pontos permanentemente sempre que chosen_build não
                # batia com a build da classe (ex: talents_json salvo sem o
                # campo "chosen_build" — ver validate_talent_allocation,
                # arquitetura/PROBLEMAS_ARQUITETURA.md).
                tt.available_points += sum(tt.allocated.values())
                tt.allocated.clear()       # talentos da build errada são inválidos
                tt.chosen_build = correct_build

        # Remove modificadores antigos
        for mod in list(tt._applied_modifiers):
            remove_modifier(cs, mod)
        tt._applied_modifiers.clear()

        # Reseta todas as flags comportamentais de talento para seus valores padrão
        for t in TALENTS.values():
            for flag in t.get("cs_flags", []):
                setattr(cs, flag["field"], flag["reset"])
        # Reseta estado de runtime (não controlado por talentos)
        cs.fire_crit_counter = 0
        cs.fire_crit_timer   = 0.0

        # Memoriza a posição atual de cada skill de talento antes de removê-las,
        # para restaurar nas mesmas posições após re-aplicar (preserva layout do usuário).
        old_positions: dict[str, int] = {}
        for i, s in enumerate(skills.skills):
            if s and getattr(s, "talent_id", None):
                old_positions[s.skill_id] = i

        # Remove habilidades de talento antigas (substitui por None para preservar comprimento da lista)
        skills.skills = [None if getattr(s, "talent_id", None) else s for s in skills.skills]
        # Remove também de learned_skill_ids para não aparecer no painel H com talento removido
        for _old_sid in tt._unlocked_skill_ids:
            skills.learned_skill_ids.discard(_old_sid)
        tt._unlocked_skill_ids.clear()

        # Aplica efeitos dos talentos alocados
        for talent_id, points in tt.allocated.items():
            t = TALENTS.get(talent_id)
            if not t:
                continue
            for eff in (t["effects"] or []):
                total_value = eff["value"] * points
                mod = Modifier(eff["attribute"], total_value, eff["type"], source="talent")
                add_modifier(cs, mod)
                tt._applied_modifiers.append(mod)
            # Habilidade desbloqueada quando atinge unlock_at (padrão = max_points)
            _unlock_at = t.get("unlock_at", t["max_points"])
            if t["unlocks_skill"] and points >= _unlock_at:
                _sid = t["unlocks_skill"]
                if _sid not in tt._unlocked_skill_ids:
                    from skill_config import SKILL_CATALOG as _SC
                    if _sid not in _SC:
                        continue  # skill não definida no catálogo — ignorar
                    # Toda skill vem do catálogo — fonte única de dados
                    new_skill = PlayerSkills._make_skill(_sid, _SC)
                    new_skill.talent_id = talent_id
                    new_skill.handler   = ""  # dispatch via skill_id (não _talent_*)
                    # Restaura na posição anterior se existir; caso contrário, primeiro None.
                    # Também remove cópia sem talent_id carregada do save (previne ícone duplicado).
                    prev_idx = old_positions.get(_sid)
                    if prev_idx is None:
                        # Procura cópia legada (save-loaded, talent_id = "") para reutilizar slot
                        for _k, _ex in enumerate(skills.skills):
                            if _ex and _ex.skill_id == _sid:
                                prev_idx = _k
                                skills.skills[_k] = None  # remove cópia duplicada
                                break
                    if prev_idx is not None and prev_idx < len(skills.skills) and skills.skills[prev_idx] is None:
                        skills.skills[prev_idx] = new_skill
                    else:
                        try:
                            idx = skills.skills.index(None)
                            skills.skills[idx] = new_skill
                        except ValueError:
                            skills.skills.append(new_skill)
                    tt._unlocked_skill_ids.add(_sid)
                    # Garante visibilidade no painel H (fonte 1 do avail list)
                    skills.learned_skill_ids.add(_sid)

        # Aplica flags comportamentais de talentos alocados (lidas por CombatSystem e SkillHandlers)
        for talent_id, points in tt.allocated.items():
            t = TALENTS.get(talent_id)
            if not t:
                continue
            for flag in t.get("cs_flags", []):
                setattr(cs, flag["field"], flag["formula"](points))

    # -----------------------------------------------------------------------
    # Eventos
    # -----------------------------------------------------------------------
    def handle_events(self, events: list, panel: pygame.Rect):
        tt = self._talent_tree()
        if not tt:
            return

        mx, my = pygame.mouse.get_pos()
        self._hovered_id = None
        self.wants_close  = False

        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and self._close_btn_rect(panel).collidepoint(event.pos):
                    self.wants_close = True
                    return
                if self._reset_btn_rect(panel).collidepoint(event.pos):
                    self.reset_talents()
                    return
                for talent_id in BUILD_TALENTS.get(tt.chosen_build, []):
                    r = self._node_rect(panel, talent_id)
                    if r.collidepoint(event.pos):
                        if event.button == 1:
                            self.allocate(talent_id)
                        elif event.button == 3:
                            self.deallocate(talent_id)

        # Hover
        for talent_id in BUILD_TALENTS.get(tt.chosen_build, []):
            if self._node_rect(panel, talent_id).collidepoint(mx, my):
                self._hovered_id = talent_id
                break

    # -----------------------------------------------------------------------
    # Render principal
    # -----------------------------------------------------------------------
    def render(self):
        tt = self._talent_tree()
        if not tt:
            return

        panel = self._panel_rect()

        # fill_surf: surfaces cacheadas — zero alocação por frame
        self.screen.blit(fill_surf(self.screen.get_size(), (0, 0, 0, 160)), (0, 0))
        self.screen.blit(fill_surf((panel.w, panel.h), C_BG), panel.topleft)
        pygame.draw.rect(self.screen, C_BORDER, panel, 2, border_radius=6)

        self._render_talent_tree(panel, tt)

        # Botão X
        close_r = self._close_btn_rect(panel)
        mx, my  = pygame.mouse.get_pos()
        pygame.draw.rect(self.screen,
                         (180, 60, 60) if close_r.collidepoint(mx, my) else (100, 35, 35),
                         close_r, border_radius=3)
        xs = self.font_md.render("X", True, (255, 255, 255))
        self.screen.blit(xs, xs.get_rect(center=close_r.center))

        # Tooltip flutuante (desenhado por cima de tudo)
        if self._hovered_id:
            self._draw_talent_tooltip(panel, self._hovered_id, tt)

    # -----------------------------------------------------------------------
    # Árvore de talentos
    # -----------------------------------------------------------------------
    def _render_talent_tree(self, panel: pygame.Rect, tt):
        build = BUILDS[tt.chosen_build]

        # Cabeçalho
        title = self.font_lg.render(f"Talentos — {build['name']}", True, C_TITLE)
        self.screen.blit(title, (panel.x + self._u(14), panel.y + self._u(10)))

        pts_col  = C_GOLD if tt.available_points > 0 else C_GRAY
        pts_surf = self.font_md.render(f"Pontos disponíveis: {tt.available_points}", True, pts_col)
        self.screen.blit(pts_surf, (panel.x + self._u(14), panel.y + self._u(36)))

        # Botão Resetar
        self._draw_button(self._reset_btn_rect(panel), "Resetar",
                          (160, 60, 60), (220, 80, 80))

        talents = BUILD_TALENTS.get(tt.chosen_build, [])

        # Linhas de conexão
        for talent_id in talents:
            t = TALENTS[talent_id]
            r_child = self._node_rect(panel, talent_id)
            for req_id in t["requires"]:
                r_parent = self._node_rect(panel, req_id)
                pts_req  = t["requires"][req_id]
                met      = tt.allocated.get(req_id, 0) >= pts_req
                color    = C_CONNECT_OK if met else C_CONNECT
                # Conexão vertical ou horizontal conforme posição relativa
                p_t = TALENTS[req_id]
                if p_t["col"] == t["col"]:
                    # mesma coluna — linha vertical
                    pygame.draw.line(self.screen, color,
                                     r_parent.midbottom, r_child.midtop, 2)
                else:
                    # colunas diferentes — linha horizontal (mesmo row)
                    pygame.draw.line(self.screen, color,
                                     r_parent.midright, r_child.midleft, 2)

        # Nós
        for talent_id in talents:
            self._render_node(panel, talent_id, tt, build["color"])

    # -----------------------------------------------------------------------
    # Slot 64×64
    # -----------------------------------------------------------------------
    def _render_node(self, panel: pygame.Rect, talent_id: str, tt, build_color: tuple):
        t       = TALENTS[talent_id]
        r       = self._node_rect(panel, talent_id)
        current = tt.allocated.get(talent_id, 0)
        maxed   = current >= t["max_points"]
        hovered = (self._hovered_id == talent_id)
        locked  = not self.can_allocate(talent_id, tt) and current == 0

        # ── Fundo ──
        if maxed:
            bg = (55, 46, 8)
        elif hovered and not locked:
            bg = (50, 40, 20)
        elif locked:
            bg = C_LOCKED
        else:
            bg = (28, 20, 10)
        pygame.draw.rect(self.screen, bg, r, border_radius=6)

        # ── Ícone representativo (rect colorido proporcional ao progresso) ──
        if current > 0:
            fill_ratio = current / t["max_points"]
            ico_h = int((self._u(NODE_H) - self._u(8)) * fill_ratio)
            ico_w = self._u(NODE_W) - self._u(8)
            ico_r = pygame.Rect(r.x + self._u(4), r.bottom - self._u(4) - ico_h, ico_w, ico_h)
            ico_col = C_MAXED if maxed else (60, 120, 200)
            self.screen.blit(fill_surf((ico_w, ico_h), (*ico_col, 80)), ico_r.topleft)

        # ── Inicial do nome centralizado ──
        initials = t["name"][:2].upper()
        txt_col  = C_LOCKED_TXT if (locked and current == 0) else \
                   C_MAXED if maxed else C_WHITE
        init_surf = self.font_lg.render(initials, True, txt_col)
        self.screen.blit(init_surf, init_surf.get_rect(center=r.center))

        # ── Contador x/max no canto superior direito ──
        counter_txt = f"{current}/{t['max_points']}"
        counter_col = C_MAXED if maxed else (C_GRAY if locked else C_WHITE)
        ctr_surf    = self.font_sm.render(counter_txt, True, counter_col)
        self.screen.blit(ctr_surf, (r.right - ctr_surf.get_width() - self._u(3), r.y + self._u(3)))

        # ── Ícone de habilidade desbloqueada (estrela dourada canto inf direito) ──
        if t["unlocks_skill"]:
            star_col = C_GOLD if maxed else (70, 56, 22)
            star_surf = self.font_sm.render("★", True, star_col)
            self.screen.blit(star_surf, (r.right - star_surf.get_width() - self._u(2),
                                         r.bottom - star_surf.get_height() - self._u(2)))

        # ── Borda ──
        if maxed:
            border_col  = C_MAXED
            border_w    = 2
        elif hovered:
            border_col  = build_color
            border_w    = 2
        elif locked:
            border_col  = C_LOCKED_BDR
            border_w    = 1
        else:
            border_col  = (90, 70, 40)
            border_w    = 1
        pygame.draw.rect(self.screen, border_col, r, border_w, border_radius=6)

    # -----------------------------------------------------------------------
    # Tooltip flutuante
    # -----------------------------------------------------------------------
    def _draw_talent_tooltip(self, panel: pygame.Rect, talent_id: str, tt):
        t       = TALENTS[talent_id]
        current = tt.allocated.get(talent_id, 0)
        mx, my  = pygame.mouse.get_pos()
        node_r  = self._node_rect(panel, talent_id)

        tooltip_w = self._u(TOOLTIP_W)

        # Monta linhas do tooltip
        lines: list[tuple[str, tuple]] = []

        # Descrição com valor vivo substituído
        desc = self._live_description(talent_id, current, tt)
        for line in self._wrap_text(desc, tooltip_w - self._u(16), self.font_tip_body):
            lines.append((line, C_WHITE))

        lines.append(("", C_GRAY))  # espaço

        # Efeitos com totais ao vivo
        if t["effects"]:
            for eff in t["effects"]:
                total = eff["value"] * current
                sign  = "+" if total >= 0 else ""
                if eff["attribute"] == "crit_rating":
                    val_str = f"{total * 100:.1f}%"
                elif eff["attribute"] == "attack_interval":
                    val_str = f"{total:.1f}s"
                else:
                    val_str = f"{total:.0f}"
                col = C_GREEN if total >= 0 else C_RED
                lines.append((f"{sign}{val_str} {eff['attribute']}", col))
            lines.append(("", C_GRAY))

        # Efeitos comportamentais ao vivo
        live = self._live_effects(talent_id, current, tt)
        for txt, col in live:
            lines.append((txt, col))
        if live:
            lines.append(("", C_GRAY))

        # Habilidade desbloqueada
        if t["unlocks_skill"] and t["skill_def"]:
            sd      = t["skill_def"]
            unlocked = current >= t["max_points"]
            hdr_col  = C_GOLD if unlocked else C_GRAY
            lines.append((f"★ {sd['name']}", hdr_col))
            for line in self._wrap_text(sd["description"], tooltip_w - self._u(24), self.font_tip_body):
                lines.append((f"  {line}", C_WHITE if unlocked else (90, 80, 60)))
            if sd["cooldown"] > 0:
                lines.append((f"  Recarga: {sd['cooldown']:.0f}s", C_GRAY))
            lines.append(("", C_GRAY))

        # Pré-requisitos
        if t["requires"]:
            lines.append(("Requer:", C_GRAY))
            for req_id, req_pts in t["requires"].items():
                cur_req = tt.allocated.get(req_id, 0)
                met     = cur_req >= req_pts
                col     = C_GREEN if met else C_RED
                lines.append((f"  {TALENTS[req_id]['name']}: {cur_req}/{req_pts}", col))
            lines.append(("", C_GRAY))

        # Dica de interação
        if self.can_allocate(talent_id, tt):
            lines.append(("Clique esq. para alocar", C_GREEN))
        if self.can_deallocate(talent_id, tt):
            lines.append(("Clique dir. para remover", C_RED))

        # Remove linhas vazias do final
        while lines and lines[-1][0] == "":
            lines.pop()

        # Mede altura necessária — dinâmico com base nas fontes reais
        line_h  = self.font_tip_body.get_height() + 3
        title_h = self.font_tip_title.get_height() + 6
        pad = self._u(8)
        tooltip_h = title_h + pad + len(lines) * line_h + pad

        # Posição: à direita do nó, se couber no painel, senão à esquerda
        tx = node_r.right + self._u(10)
        ty = node_r.y
        if tx + tooltip_w > panel.right - self._u(5):
            tx = node_r.left - tooltip_w - self._u(10)
        if ty + tooltip_h > panel.bottom - self._u(5):
            ty = panel.bottom - tooltip_h - self._u(5)
        ty = max(panel.y + self._u(5), ty)

        bg_r = pygame.Rect(tx, ty, tooltip_w, tooltip_h)
        self.screen.blit(fill_surf((bg_r.w, bg_r.h), (16, 12, 6, 240)), bg_r.topleft)
        pygame.draw.rect(self.screen, C_BORDER, bg_r, 1, border_radius=4)

        # Título (nome + pts)
        pts_col   = C_MAXED if current >= t["max_points"] else C_TITLE
        name_surf = self.font_tip_title.render(t["name"], True, pts_col)
        pts_str   = f"{current}/{t['max_points']}"
        pts_surf  = self.font_tip_body.render(pts_str, True, pts_col)
        self.screen.blit(name_surf, (tx + pad, ty + pad // 2 + self._u(2)))
        self.screen.blit(pts_surf,  (tx + tooltip_w - pts_surf.get_width() - pad,
                                     ty + pad // 2 + self._u(4)))

        pygame.draw.line(self.screen, C_BORDER,
                         (tx + self._u(4), ty + title_h), (tx + tooltip_w - self._u(4), ty + title_h))

        # Linhas
        ly = ty + title_h + pad // 2
        for text, col in lines:
            if text == "":
                ly += self._u(4)
                continue
            surf = self.font_tip_body.render(text, True, col)
            self.screen.blit(surf, (tx + pad, ly))
            ly += line_h

    # -----------------------------------------------------------------------
    # Descrição ao vivo (substitui {v} pelo valor atual)
    # -----------------------------------------------------------------------
    def _live_description(self, talent_id: str, current: int, tt) -> str:
        t    = TALENTS[talent_id]
        desc = t["description"]
        if "{v}" not in desc:
            return desc

        # Sempre mostra o que o próximo ponto fará; se já estiver no máximo, mostra o valor atual
        display = min(current + 1, t["max_points"])

        # Talentos com effects usam o valor acumulado do primeiro efeito
        if t["effects"]:
            total = t["effects"][0]["value"] * display
            attr  = t["effects"][0]["attribute"]
            # crit_rating armazenado como fração (0.01 = 1%)
            _FRAC_PERCENT_ATTRS = {"crit_rating", "haste_rating"}
            # parry/dodge/hit/block armazenados em rating points (20 pts = 1%)
            _RATING_ATTRS = {"parry_rating", "dodge_rating", "hit_rating", "block_rating"}
            if attr in _FRAC_PERCENT_ATTRS:
                v_str = f"{total * 100:.1f}"
            elif attr in _RATING_ATTRS:
                v_str = f"{total / 20:.1f}"
            elif attr == "attack_interval":
                v_str = f"{total:.1f}"
            else:
                v_str = f"{total:.0f}"
            return desc.replace("{v}", v_str)

        # Talentos comportamentais — usa preview_formula definida em talent_data.py
        preview_fn = t.get("preview_formula")
        v = preview_fn(display) if preview_fn else display
        return desc.replace("{v}", str(v))

    # -----------------------------------------------------------------------
    # Efeitos comportamentais ao vivo (Veterano, Sede de Batalha, etc.)
    # -----------------------------------------------------------------------
    def _live_effects(self, talent_id: str, current: int, tt) -> list[tuple[str, tuple]]:
        from components import CombatStats as _CS_LE
        cs = self.world.get_component(self.player_id, _CS_LE)
        result = []
        if talent_id == "cav_veterano":
            custo_ef = cs.golpe_poderoso_rage_cost if cs else max(10, 15 - current)
            result.append((f"Custo atual de Golpe Poderoso: {custo_ef} Raiva",
                            C_GREEN if current > 0 else C_GRAY))
        elif talent_id == "cav_sede_batalha":
            reducao = cs.interceptar_cooldown_reduction if cs else current * 2.0
            cd_ef   = max(0, 22 - reducao)
            result.append((f"Cooldown atual de Interceptar: {cd_ef:.0f}s",
                            C_GREEN if current > 0 else C_GRAY))
        elif talent_id == "cav_vontade":
            if current >= 1:
                result.append(("Interceptar gera +10 Raiva", C_GREEN))
            else:
                result.append(("Interceptar não gera Raiva", C_GRAY))
        elif talent_id == "cav_alvo_confirmado":
            stun = cs.interceptar_stun_duration if cs else current * 0.3
            if current > 0:
                result.append((f"Stun do Interceptar: {stun:.1f}s", C_GREEN))
        elif talent_id == "cav_embalo":
            bonus = int(round((cs.embalo_bonus_per_charge if cs else 0.0) * 100))
            if current > 0:
                result.append((f"Golpe Poderoso após crítico: +{bonus}% dano", C_GREEN))
        elif talent_id == "cav_explorador":
            bonus = (cs.explorador_crit_per_point if cs else current) * 15
            if current > 0:
                result.append((f"Crit vs alvos debilitados: +{bonus}%", C_GREEN))
        elif talent_id == "cav_foco_mortal":
            if current >= 1:
                result.append(("Dano cresce +8%/s vs alvo debilitado (máx 40%)", C_GREEN))
                result.append(("Acumula enquanto o slow estiver ativo; reseta ao expirar", C_GRAY))
        elif talent_id == "cav_provocacao":
            if current >= 1:
                result.append(("Raio: 3 tiles | Duração: 10s | Cooldown: 45s", C_GREEN))
                result.append(("Enlouquecidos: +5% dano causado / +10% dano recebido", C_GREEN))
        return result

    # -----------------------------------------------------------------------
    # Botão genérico
    # -----------------------------------------------------------------------
    def _draw_button(self, r: pygame.Rect, text: str, bg_col, hover_col):
        mx, my = pygame.mouse.get_pos()
        col = hover_col if r.collidepoint(mx, my) else bg_col
        pygame.draw.rect(self.screen, col, r, border_radius=3)
        pygame.draw.rect(self.screen, C_BORDER, r, 1, border_radius=3)
        s = self.font_sm.render(text, True, (230, 230, 230))
        self.screen.blit(s, s.get_rect(center=r.center))

    # -----------------------------------------------------------------------
    # Utilitário
    # -----------------------------------------------------------------------
    @staticmethod
    def _wrap_text(text: str, max_width: int, font: pygame.font.Font) -> list[str]:
        words = text.split()
        lines, current = [], ""
        for word in words:
            test = (current + " " + word).strip()
            if font.size(test)[0] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

