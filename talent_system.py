"""
Sistema de Árvore de Talentos
==============================
Gerencia UI e lógica de alocação da árvore de talentos do Cavaleiro.
"""

from __future__ import annotations
import pygame
from talent_data import BUILDS, TALENTS, BUILD_TALENTS
from combat_log import LOG
from save_system import request_autosave


# ---------------------------------------------------------------------------
# Constantes de UI
# ---------------------------------------------------------------------------
PANEL_W       = 800
PANEL_H       = 640
NODE_W        = 64
NODE_H        = 64
NODE_COL_GAP  = 96    # distância centro-a-centro horizontal
NODE_ROW_GAP  = 90    # distância centro-a-centro vertical
GRID_ORIGIN_X = 60    # margem esquerda dentro do painel
GRID_ORIGIN_Y = 70    # margem superior dentro do painel

TOOLTIP_W     = 280   # largura do tooltip flutuante

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


class TalentSystem:
    """Gerencia a UI e a lógica de alocação da árvore de talentos."""

    def __init__(self, world, player_entity_id: int, screen: pygame.Surface):
        self.world       = world
        self.player_id   = player_entity_id
        self.screen      = screen
        self.font_lg     = pygame.font.Font(None, 26)
        self.font_md     = pygame.font.Font(None, 20)
        self.font_sm     = pygame.font.Font(None, 17)
        self._hovered_id: str | None = None
        self.wants_close: bool = False

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    def _talent_tree(self):
        from components import TalentTree
        return self.world.get_component(self.player_id, TalentTree)

    def _panel_rect(self) -> pygame.Rect:
        sw, sh = self.screen.get_size()
        return pygame.Rect((sw - PANEL_W) // 2, (sh - PANEL_H) // 2, PANEL_W, PANEL_H)

    def _node_rect(self, panel: pygame.Rect, talent_id: str) -> pygame.Rect:
        t = TALENTS[talent_id]
        x = panel.x + GRID_ORIGIN_X + t["col"] * NODE_COL_GAP
        y = panel.y + GRID_ORIGIN_Y + t["row"] * NODE_ROW_GAP
        return pygame.Rect(x, y, NODE_W, NODE_H)

    def _close_btn_rect(self, panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.right - 34, panel.y + 4, 30, 30)

    def _reset_btn_rect(self, panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.right - 130, panel.y + 8, 90, 22)

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

    def deallocate(self, talent_id: str):
        tt = self._talent_tree()
        if not tt or not self.can_deallocate(talent_id, tt):
            return
        tt.allocated[talent_id] -= 1
        tt.available_points += 1
        self.apply_talent_effects()
        request_autosave()

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

    # -----------------------------------------------------------------------
    # Aplicação de efeitos
    # -----------------------------------------------------------------------
    def apply_talent_effects(self):
        from components import TalentTree, CombatStats, PlayerSkills, Modifier
        tt     = self._talent_tree()
        cs     = self.world.get_component(self.player_id, CombatStats)
        skills = self.world.get_component(self.player_id, PlayerSkills)
        if not tt or not cs or not skills:
            return

        # Remove modificadores antigos
        for mod in list(tt._applied_modifiers):
            cs.remove_modifier(mod)
        tt._applied_modifiers.clear()

        # Reseta flags comportamentais antes de re-aplicar
        cs.explorador_crit_per_point = 0
        cs.foco_mortal_enabled       = False
        cs.embalo_on_crit            = False
        cs.pnq_enabled               = False
        cs.embalo_bonus_per_charge    = 0.0
        cs.golpe_poderoso_rage_cost   = 15
        cs.interceptar_cooldown_reduction = 0.0
        cs.interceptar_stun_duration  = 0.0
        cs.interceptar_rage_bonus     = 0
        cs.pnq_stun_duration          = 1.0
        cs.impacto_maquina_matar      = False
        cs.impacto_assassino          = False
        cs.executar_horrorizante      = False

        # Memoriza a posição atual de cada skill de talento antes de removê-las,
        # para restaurar nas mesmas posições após re-aplicar (preserva layout do usuário).
        old_positions: dict[str, int] = {}
        for i, s in enumerate(skills.skills):
            if s and getattr(s, "talent_id", None):
                old_positions[s.skill_id] = i

        # Remove habilidades de talento antigas
        skills.skills = [s for s in skills.skills if not getattr(s, "talent_id", None)]
        tt._unlocked_skill_ids.clear()

        # Aplica efeitos dos talentos alocados
        for talent_id, points in tt.allocated.items():
            t = TALENTS.get(talent_id)
            if not t:
                continue
            for eff in (t["effects"] or []):
                total_value = eff["value"] * points
                mod = Modifier(eff["attribute"], total_value, eff["type"])
                cs.add_modifier(mod)
                tt._applied_modifiers.append(mod)
            # Habilidade desbloqueada quando atinge unlock_at (padrão = max_points)
            _unlock_at = t.get("unlock_at", t["max_points"])
            if t["unlocks_skill"] and points >= _unlock_at:
                if t["skill_def"] and t["unlocks_skill"] not in tt._unlocked_skill_ids:
                    from components import Skill
                    sd = t["skill_def"]
                    new_skill = Skill(sd["name"], sd["description"], sd["cooldown"])
                    new_skill.talent_id  = talent_id
                    new_skill.handler    = t["unlocks_skill"]
                    new_skill.skill_id   = t["unlocks_skill"]
                    new_skill.icon_name  = f"skill_{t['unlocks_skill']}"
                    new_skill.sound_name = sd.get("sound") or f"skill_{t['unlocks_skill']}"
                    if sd.get("max_charges") is not None:
                        new_skill.max_charges    = sd["max_charges"]
                        new_skill.charges        = 0
                        new_skill.charge_timeout = sd.get("charge_timeout", 0.0)
                    # Restaura na posição anterior se existir; caso contrário, primeiro None
                    prev_idx = old_positions.get(t["unlocks_skill"])
                    if prev_idx is not None and prev_idx < len(skills.skills) and skills.skills[prev_idx] is None:
                        skills.skills[prev_idx] = new_skill
                    else:
                        try:
                            idx = skills.skills.index(None)
                            skills.skills[idx] = new_skill
                        except ValueError:
                            skills.skills.append(new_skill)
                    tt._unlocked_skill_ids.add(t["unlocks_skill"])

        # Atualiza flags comportamentais de talento (lidas por CombatSystem e SkillHandlers)
        cs.explorador_crit_per_point      = tt.allocated.get("cav_explorador", 0)
        cs.foco_mortal_enabled            = tt.allocated.get("cav_foco_mortal", 0) >= 1
        cs.embalo_on_crit                 = tt.allocated.get("cav_embalo", 0) > 0
        cs.pnq_enabled                    = tt.allocated.get("cav_punho_queixo", 0) >= 1
        cs.embalo_bonus_per_charge        = tt.allocated.get("cav_embalo", 0) * 0.10
        cs.golpe_poderoso_rage_cost       = max(10, 15 - tt.allocated.get("cav_veterano", 0))
        cs.interceptar_cooldown_reduction = tt.allocated.get("cav_sede_batalha", 0) * 2.0
        cs.interceptar_stun_duration      = tt.allocated.get("cav_alvo_confirmado", 0) * 0.3
        cs.interceptar_rage_bonus         = 10 if tt.allocated.get("cav_vontade", 0) >= 1 else 0
        cs.pnq_stun_duration              = float(max(1, tt.allocated.get("cav_punho_queixo", 1)))
        cs.impacto_maquina_matar          = tt.allocated.get("cav_maquina_matar", 0) >= 1
        cs.impacto_assassino              = tt.allocated.get("cav_assassino", 0) >= 1
        cs.executar_horrorizante          = tt.allocated.get("cav_horrorizante", 0) >= 1

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

        # Fundo semi-transparente
        overlay = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        # Painel
        bg = pygame.Surface((panel.w, panel.h), pygame.SRCALPHA)
        bg.fill(C_BG)
        self.screen.blit(bg, panel.topleft)
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
        self.screen.blit(title, (panel.x + 14, panel.y + 10))

        pts_col  = C_GOLD if tt.available_points > 0 else C_GRAY
        pts_surf = self.font_md.render(
            f"Pontos disponíveis: {tt.available_points}", True, pts_col)
        self.screen.blit(pts_surf, (panel.x + 14, panel.y + 36))

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
            ico_h = int((NODE_H - 8) * fill_ratio)
            ico_r = pygame.Rect(r.x + 4, r.bottom - 4 - ico_h, NODE_W - 8, ico_h)
            ico_col = C_MAXED if maxed else (60, 120, 200)
            ico_surf = pygame.Surface((ico_r.w, ico_r.h), pygame.SRCALPHA)
            ico_surf.fill((*ico_col, 80))
            self.screen.blit(ico_surf, ico_r.topleft)

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
        self.screen.blit(ctr_surf, (r.right - ctr_surf.get_width() - 3, r.y + 3))

        # ── Ícone de habilidade desbloqueada (estrela dourada canto inf direito) ──
        if t["unlocks_skill"]:
            star_col = C_GOLD if maxed else (70, 56, 22)
            star_surf = self.font_sm.render("★", True, star_col)
            self.screen.blit(star_surf, (r.right - star_surf.get_width() - 2,
                                         r.bottom - star_surf.get_height() - 2))

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

        # Monta linhas do tooltip
        lines: list[tuple[str, tuple]] = []

        # Descrição com valor vivo substituído
        desc = self._live_description(talent_id, current, tt)
        for line in self._wrap_text(desc, TOOLTIP_W - 16, self.font_sm):
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
            for line in self._wrap_text(sd["description"], TOOLTIP_W - 24, self.font_sm):
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

        # Mede altura necessária
        line_h = 15
        title_h = 22
        pad = 8
        tooltip_h = title_h + pad + len(lines) * line_h + pad

        # Posição: à direita do nó, se couber no painel, senão à esquerda
        tx = node_r.right + 10
        ty = node_r.y
        if tx + TOOLTIP_W > panel.right - 5:
            tx = node_r.left - TOOLTIP_W - 10
        if ty + tooltip_h > panel.bottom - 5:
            ty = panel.bottom - tooltip_h - 5
        ty = max(panel.y + 5, ty)

        # Fundo
        bg_r = pygame.Rect(tx, ty, TOOLTIP_W, tooltip_h)
        bg_surf = pygame.Surface((bg_r.w, bg_r.h), pygame.SRCALPHA)
        bg_surf.fill((16, 12, 6, 240))
        self.screen.blit(bg_surf, bg_r.topleft)
        pygame.draw.rect(self.screen, C_BORDER, bg_r, 1, border_radius=4)

        # Título (nome + pts)
        pts_col   = C_MAXED if current >= t["max_points"] else C_TITLE
        name_surf = self.font_md.render(t["name"], True, pts_col)
        pts_str   = f"{current}/{t['max_points']}"
        pts_surf  = self.font_sm.render(pts_str, True, pts_col)
        self.screen.blit(name_surf, (tx + pad, ty + pad // 2 + 2))
        self.screen.blit(pts_surf,  (tx + TOOLTIP_W - pts_surf.get_width() - pad,
                                     ty + pad // 2 + 4))

        pygame.draw.line(self.screen, C_BORDER,
                         (tx + 4, ty + title_h), (tx + TOOLTIP_W - 4, ty + title_h))

        # Linhas
        ly = ty + title_h + pad // 2
        for text, col in lines:
            if text == "":
                ly += 4
                continue
            surf = self.font_sm.render(text, True, col)
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

