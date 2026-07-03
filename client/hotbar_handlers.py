"""
hotbar_handlers.py — Mixin com a hotbar de habilidades (slots 1-4),
barra de consumíveis (cliques) e vinheta de HP baixo: detecção de
clique em slots, bloqueio de skill por talento não alocado, e o
desenho completo da hotbar (ícones, cooldown, GCD, custo de fúria,
indicador de proc, drag-and-drop com remoção). Inclui as constantes
de geometria _HB_* e as cores de fallback _SKILL_FALLBACK_COLORS.
Separado de game.py para manter GameEngine conciso. Esta classe NÃO
deve ser instanciada diretamente — ela é herdada por GameEngine, que
fornece self.world, self.player_entity, self.screen, self.systems,
self._skill_system e os demais atributos referenciados aqui.
"""
import math
import pygame
from ui_helpers import fill_surf

from components import CombatState, CombatStats, PlayerSkills
from icon_manager import ICONS
from systems import ConsumableSystem
from ui_sizes import UI

# Lookup reverso: skill_id → (talent_id, talent_name, min_points_to_unlock)
# Gerado dinamicamente a partir de talent_data.TALENTS.
from talent_data import TALENTS as _TT_DATA
_TALENT_SKILL_REQS: dict[str, tuple[str, str, int]] = {
    td["unlocks_skill"]: (tid, td["name"], td.get("unlock_at", 1))
    for tid, td in _TT_DATA.items()
    if td.get("unlocks_skill")
}


class HotbarHandlers:

    # ------------------------------------------------------------------
    # HUD
    # ------------------------------------------------------------------
    # Hotbar de habilidades (1-4)
    # ------------------------------------------------------------------

    def _set_hotbar_row_scale(self) -> None:
        """Calcula o clamp de escala pra LINHA INTEIRA hotbar+consumable bar
        (lado a lado, mesmo HB_W/HB_PAD) — usa a capacidade MÁXIMA de cada
        barra (não só os slots ocupados), senão hotbar e consumable bar
        clampariam em escalas diferentes entre si e os slots ficariam com
        tamanhos visualmente inconsistentes entre as duas barras. Chamar no
        início de qualquer método que leia self._HB_W/_HB_H/_HB_ICO/_HB_PAD."""
        from skill_config import NUM_SLOTS as _NS_HB
        from components import ConsumableBar as _CB_HB
        BASE_W, BASE_PAD, BASE_GAP = UI.HOTBAR_SLOT_W, UI.HOTBAR_PAD, UI.HOTBAR_ROW_GAP
        hotbar_w = _NS_HB * BASE_W + (_NS_HB - 1) * BASE_PAD
        cons_w   = _CB_HB.NUM_SLOTS * BASE_W + (_CB_HB.NUM_SLOTS - 1) * BASE_PAD
        self._set_panel_scale(hotbar_w + BASE_GAP + cons_w, BASE_W)

    # Propriedades (não atributos fixos) — precisam reagir a self._ui_scale
    # em runtime, e hotbar/consumable_bar/habilidades leem essas 4 via
    # self._HB_*, então não dá pra virar atributo de classe fixo.
    @property
    def _HB_W(self) -> int:
        return self._u(UI.HOTBAR_SLOT_W)

    @property
    def _HB_H(self) -> int:
        return self._u(UI.HOTBAR_SLOT_H)

    @property
    def _HB_ICO(self) -> int:
        return self._u(UI.HOTBAR_ICON)

    @property
    def _HB_PAD(self) -> int:
        return self._u(UI.HOTBAR_PAD)

    _SKILL_FALLBACK_COLORS = [
        (180,  60,  60),   # 1 Golpe Poderoso
        ( 60, 180,  80),   # 2 Cura
        (200, 130,   0),   # 3 Impacto
        (140,  60, 200),   # 4 Executar
    ]

    def _is_talent_locked(self, skill_id: str) -> bool:
        """True se a skill requer talento e ele não tem pontos alocados suficientes."""
        if skill_id not in _TALENT_SKILL_REQS:
            return False
        talent_id, _name, min_pts = _TALENT_SKILL_REQS[skill_id]
        from components import TalentTree as _TTree
        tt = self.world.get_component(self.player_entity, _TTree)
        if tt is None:
            return False
        return tt.allocated.get(talent_id, 0) < min_pts

    def _handle_hotbar_click(self, event):
        """Aciona habilidade ao clicar com botão esquerdo em slot da hotbar."""
        self._set_hotbar_row_scale()
        # Shift+click → drag de remoção, não usa skill
        if pygame.key.get_mods() & pygame.KMOD_SHIFT:
            return
        from components import PlayerSkills
        player_skills = self.world.get_component(self.player_entity, PlayerSkills)
        if not player_skills:
            return
        occupied = [(i, s) for i, s in enumerate(player_skills.skills) if s is not None]
        if not occupied:
            return
        n_occ   = len(occupied)
        total_w = n_occ * self._HB_W + (n_occ - 1) * self._HB_PAD
        x0      = self.screen.get_width()  // 2 - total_w // 2
        y0      = self.screen.get_height() - self._HB_H - self._u(10)
        mx, my  = event.pos
        for j, (i, skill) in enumerate(occupied):
            sx = x0 + j * (self._HB_W + self._HB_PAD)
            if pygame.Rect(sx, y0, self._HB_W, self._HB_H).collidepoint(mx, my):
                # Talento removido → skill bloqueada
                if skill.skill_id and self._is_talent_locked(skill.skill_id):
                    skill.fail_flash_timer = 0.2
                    break
                if not self._skill_system._use_skill(i, skill):
                    skill.fail_flash_timer = 0.2
                break

    def _handle_consumable_bar_click(self, event) -> None:
        """Usa consumível ao clicar com botão esquerdo em slot da barra de consumíveis."""
        self._set_hotbar_row_scale()
        from components import ConsumableBar as _CB, PlayerSkills as _PS
        cbar = self.world.get_component(self.player_entity, _CB)
        if not cbar:
            return

        cons_occ = [(i, cbar.slots[i]) for i in range(_CB.NUM_SLOTS) if cbar.slots[i]]
        if not cons_occ:
            return

        ps           = self.world.get_component(self.player_entity, _PS)
        n_skills_occ = sum(1 for s in ps.skills if s) if ps else 0
        skills_w     = n_skills_occ * self._HB_W + max(0, n_skills_occ - 1) * self._HB_PAD
        x0           = self.screen.get_width() // 2 - skills_w // 2 + skills_w + 20
        y0           = self.screen.get_height() - self._HB_H - self._u(10)
        mx, my       = event.pos

        for j, (i, item_name) in enumerate(cons_occ):
            sx = x0 + j * (self._HB_W + self._HB_PAD)
            if pygame.Rect(sx, y0, self._HB_W, self._HB_H).collidepoint(mx, my):
                if cbar.global_cooldown <= 0:
                    for sys in self.systems:
                        if isinstance(sys, ConsumableSystem):
                            sys._use_consumable(self.player_entity, item_name, cbar)
                            break
                break

    # rage_cost e proc_attr são agora lidos diretamente do objeto Skill (via skill_config.py)

    def _draw_low_hp_vignette(self) -> None:
        """Vinheta vermelha pulsante na borda da tela quando HP < 30%."""
        from components import CombatStats as _CS_V
        cs = self.world.get_component(self.player_entity, _CS_V)
        if not cs or cs.max_hp <= 0:
            return
        ratio = cs.current_hp / cs.max_hp
        if ratio >= 0.30:
            return
        # Pulso senoidal: 0.0 → 1.0 → 0.0, ~1 ciclo/s
        pulse = (math.sin(pygame.time.get_ticks() * 0.005) + 1.0) * 0.5
        # Intensifica quanto mais baixo o HP: 0% HP → alpha máx 190; 30% → 0
        intensity = 1.0 - (ratio / 0.30)
        alpha = int(pulse * intensity * 190)
        if alpha <= 0:
            return
        sw, sh = self.screen.get_width(), self.screen.get_height()
        bw = 28
        surf = pygame.Surface((sw, sh), pygame.SRCALPHA)
        col = (200, 20, 20, alpha)
        pygame.draw.rect(surf, col, (0,       0,       sw, bw))
        pygame.draw.rect(surf, col, (0,       sh - bw, sw, bw))
        pygame.draw.rect(surf, col, (0,       0,       bw, sh))
        pygame.draw.rect(surf, col, (sw - bw, 0,       bw, sh))
        self.screen.blit(surf, (0, 0))

    def _draw_hotbar(self):
        player_skills = self.world.get_component(self.player_entity, PlayerSkills)
        if not player_skills:
            return
        self._set_hotbar_row_scale()

        # --- Rage atual do jogador e CombatStats (para custos modificados por talentos) ---
        from components import CharacterStats as _CS
        _char    = self.world.get_component(self.player_entity, _CS)
        _cs_hb   = self.world.get_component(self.player_entity, CombatStats)
        player_rage = _char.rage if _char else 0

        # --- HP% do alvo para proc do Executar e tooltip ---
        target_hp_ratio = 1.0
        combat_state = self.world.get_component(self.player_entity, CombatState)
        if combat_state and combat_state.target_entity_id != -1:
            _tgt_local = combat_state.target_entity_id
            tgt_cs = self.world.get_component(_tgt_local, CombatStats)
            if tgt_cs and tgt_cs.max_hp > 0:
                # Offline ou mob local com CombatStats
                target_hp_ratio = tgt_cs.current_hp / tgt_cs.max_hp
            else:
                # Mob remoto — HP em RemoteEntityMeta
                _meta_hb = self._meta_from_local(_tgt_local)
                if _meta_hb and _meta_hb.hp_max > 0:
                    target_hp_ratio = _meta_hb.hp / _meta_hb.hp_max
                else:
                    # Player remoto (PvP) — HP em RemoteControlled
                    from components import RemoteControlled as _RCratio
                    _rc_ratio = self.world.get_component(_tgt_local, _RCratio)
                    if _rc_ratio and _rc_ratio.hp_max > 0:
                        target_hp_ratio = _rc_ratio.hp / _rc_ratio.hp_max

        # Pulso animado para o brilho (0..1, ciclo ~1.6s)
        pulse = (math.sin(pygame.time.get_ticks() / 250.0) + 1) / 2

        # Channeling de Fatiador de Corpos — bloqueia visualmente todas as skills
        channeling = _char is not None and _char.fatiador_timer > 0

        # === Drag da hotbar (reordenar / Shift+drag para remover) ===
        from skill_config import NUM_SLOTS as _NS_HB
        _hb_events  = self._ui_events
        _mods_hb    = pygame.key.get_mods()
        _shift_hb   = bool(_mods_hb & pygame.KMOD_SHIFT)
        mx, my      = pygame.mouse.get_pos()

        # Posições dos slots — pré-calculadas para uso no drag
        _drag_hb = self._get_drag()
        _dragging_skill = (_drag_hb.kind == "skill" and _drag_hb.source == "habilidades"
                          and _drag_hb.payload is not None)
        _hb_self_dragging = (_drag_hb.kind == "skill" and _drag_hb.source == "hotbar"
                            and _drag_hb.source_idx != -1)
        _hb_drag_show_all = _dragging_skill or (_hb_self_dragging and _drag_hb.active)
        if _hb_drag_show_all:
            occupied = [(i, player_skills.skills[i]) for i in range(_NS_HB)]
        else:
            occupied = [(i, s) for i, s in enumerate(player_skills.skills) if s is not None]
        n_occ   = len(occupied)
        if n_occ == 0:
            return
        total_w = n_occ * self._HB_W + (n_occ - 1) * self._HB_PAD
        x0      = self.screen.get_width()  // 2 - total_w // 2
        y0      = self.screen.get_height() - self._HB_H - self._u(10)

        def _hb_slot_rect(j):
            sx = x0 + j * (self._HB_W + self._HB_PAD)
            return pygame.Rect(sx, y0, self._HB_W, self._HB_H)

        # MOUSEDOWN → registrar início de drag pendente
        for _ev_hb in _hb_events:
            if _ev_hb.type == pygame.MOUSEBUTTONDOWN and _ev_hb.button == 1:
                for _jj, (_ii, _sk) in enumerate(occupied):
                    if _sk is not None and _hb_slot_rect(_jj).collidepoint(_ev_hb.pos):
                        _drag_hb.kind       = "skill"
                        _drag_hb.source     = "hotbar"
                        _drag_hb.source_idx = _ii
                        _drag_hb.payload    = _sk.skill_id
                        _drag_hb.shift      = _shift_hb
                        _drag_hb.start_pos  = _ev_hb.pos
                        _drag_hb.active     = _shift_hb  # Shift → ativa imediatamente
                        break
                break

        # MOUSEMOTION → ativar drag ao superar threshold de 8px
        _hb_self_dragging = (_drag_hb.kind == "skill" and _drag_hb.source == "hotbar"
                             and _drag_hb.source_idx != -1)
        if _hb_self_dragging and not _drag_hb.active and _drag_hb.start_pos is not None:
            _dx = mx - _drag_hb.start_pos[0]
            _dy = my - _drag_hb.start_pos[1]
            if _dx * _dx + _dy * _dy > 64:
                _drag_hb.active = True
                # Recalcular occupied p/ mostrar todos os slots
                occupied = [(i, player_skills.skills[i]) for i in range(_NS_HB)]
                n_occ    = len(occupied)
                total_w  = n_occ * self._HB_W + (n_occ - 1) * self._HB_PAD
                x0       = self.screen.get_width() // 2 - total_w // 2

        # MOUSEUP → confirmar drag ou resetar
        for _ev_hb in _hb_events:
            if _ev_hb.type == pygame.MOUSEBUTTONUP and _ev_hb.button == 1:
                if _hb_self_dragging and _drag_hb.active:
                    _di   = _drag_hb.source_idx
                    _n_occ_full = _NS_HB  # sempre full para drop targets
                    _full_occ   = [(i, player_skills.skills[i]) for i in range(_n_occ_full)]
                    _full_w     = _n_occ_full * self._HB_W + (_n_occ_full - 1) * self._HB_PAD
                    _full_x0    = self.screen.get_width() // 2 - _full_w // 2

                    if _drag_hb.shift:
                        # Verificar se soltou FORA da barra
                        _inside_bar = any(
                            pygame.Rect(_full_x0 + _jj2 * (self._HB_W + self._HB_PAD),
                                        y0, self._HB_W, self._HB_H).collidepoint(_ev_hb.pos)
                            for _jj2 in range(_n_occ_full)
                        )
                        if not _inside_bar:
                            player_skills.skills[_di] = None
                            self._save_config()
                    else:
                        # Reordenar: checar slot de destino
                        for _jj2, (_ii2, _sk2) in enumerate(_full_occ):
                            _tr = pygame.Rect(_full_x0 + _jj2 * (self._HB_W + self._HB_PAD),
                                              y0, self._HB_W, self._HB_H)
                            if _tr.collidepoint(_ev_hb.pos) and _ii2 != _di:
                                # Swap
                                player_skills.skills[_di], player_skills.skills[_ii2] = \
                                    player_skills.skills[_ii2], player_skills.skills[_di]
                                self._save_config()
                                break
                # Reset drag state
                if _hb_self_dragging:
                    _drag_hb.reset()
                break

        for j, (i, skill) in enumerate(occupied):
            sx = x0 + j * (self._HB_W + self._HB_PAD)
            r  = pygame.Rect(sx, y0, self._HB_W, self._HB_H)

            # Slot vazio exibido durante drag — apenas fundo destacado
            if skill is None:
                pygame.draw.rect(self.screen, (38, 32, 16), r, border_radius=5)
                pygame.draw.rect(self.screen, (160, 130, 50), r, 2, border_radius=5)
                # Mostra o atalho configurado (não o índice padrão 1-0)
                _kb_empty = pygame.key.name(player_skills.keybinds[i]).upper()
                num_s = self.font_xs.render(_kb_empty, True, (100, 85, 48))
                self.screen.blit(num_s, (sx + 4, y0 + 4))
                continue

            # --- Estado de proc por skill ---
            # Charge-based: proc = tem cargas disponíveis
            if skill.max_charges > 0:
                is_procced   = skill.charges > 0
                visual_ready = is_procced
            elif skill.skill_id == "executar":
                # Proc composto: carga livre (Assassino) OU HP% baixo do alvo
                free_charge  = _char is not None and _char.free_executar_charges > 0
                rage_ok      = player_rage >= skill.rage_cost
                has_target   = (combat_state is not None and
                                combat_state.target_entity_id != -1 and
                                target_hp_ratio > 0)
                hp_proc      = rage_ok and has_target and target_hp_ratio <= 0.30

                # is_procced: mostra glow (sinaliza proc disponível, mesmo sem alvo/rage)
                is_procced   = free_charge or (has_target and target_hp_ratio <= 0.30)

                # visual_ready: skill TOTALMENTE utilizável agora (todas as condições)
                # — free charge: precisa de alvo vivo
                # — hp_proc: precisa de alvo vivo + rage suficiente
                visual_ready = (free_charge and has_target) or hp_proc
            elif skill.proc_attr:
                # Proc genérico: lê atributo declarado em skill_config.py
                is_procced   = _char is not None and getattr(_char, skill.proc_attr, 0) > 0
                visual_ready = skill.is_ready()
            else:
                is_procced   = False
                visual_ready = skill.is_ready()

            # Choque Térmico: brilho em skills ofensivas de escola fogo quando alvo tem root
            if (not is_procced
                    and getattr(skill, "school", "") == "fogo"
                    and getattr(skill, "offensive", True)
                    and _char is not None
                    and getattr(_char, "thermal_shock_active", False)):
                is_procced = True

            # --- Efeito de brilho (proc) — desenhado antes do slot ---
            if is_procced:
                glow_r = int(180 + 75 * pulse)
                glow_g = int(120 + 60 * pulse)
                glow_b = int(20  + 30 * pulse)
                for expand in range(5, 0, -1):
                    gr    = r.inflate(expand * 2, expand * 2)
                    gs    = pygame.Surface((gr.width, gr.height), pygame.SRCALPHA)
                    alpha = max(0, int((50 + 100 * pulse) * (1.0 - expand / 6)))
                    gs.fill((glow_r, glow_g, glow_b, alpha))
                    self.screen.blit(gs, gr.topleft)

            # --- Fundo do slot ---
            bg = (35, 28, 14) if visual_ready else (20, 15, 8)
            pygame.draw.rect(self.screen, bg, r, border_radius=5)

            # --- Ícone ---
            ic        = self._HB_ICO
            pad       = (self._HB_W - ic) // 2
            icon_r    = pygame.Rect(sx + pad, y0 + pad, ic, ic)
            _icon_key = (ICONS.skill_key_by_name(skill.icon_name)
                         if skill.icon_name else ICONS.skill_key(i))
            icon_surf = ICONS.get(_icon_key, ic)
            if icon_surf:
                self.screen.blit(icon_surf, icon_r)
            else:
                fb      = self._SKILL_FALLBACK_COLORS[i % len(self._SKILL_FALLBACK_COLORS)]
                alpha   = 200 if visual_ready else 80
                self.screen.blit(fill_surf((ic, ic), (*fb, alpha)), icon_r)

            # --- Overlay de cooldown / inatividade / carga ---
            if skill.skill_id == "executar":
                if skill.current_cooldown > 0:
                    # Em cooldown — overlay normal com contador
                    # area=: recorta a surface full-size cacheada na altura do
                    # cooldown — 1 entrada de cache p/ qualquer ov_h (sem churn)
                    cd_ratio = skill.current_cooldown / max(0.001, skill.cooldown)
                    ov_h     = int(self._HB_H * cd_ratio)
                    self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 160)),
                                     (sx, y0), area=pygame.Rect(0, 0, self._HB_W, ov_h))
                    cd_surf = self.font_sm.render(f"{skill.current_cooldown:.1f}", True, (220, 200, 130))
                    self.screen.blit(cd_surf, (sx + self._HB_W // 2 - cd_surf.get_width() // 2,
                                               y0 + self._HB_H // 2 - cd_surf.get_height() // 2))
                elif not is_procced:
                    # Sem condição de proc — inativo (alvo HP alto e sem carga)
                    self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 160)), (sx, y0))
                elif not visual_ready:
                    # Proc disponível mas não totalmente utilizável (sem alvo ou rage insuf.)
                    # Mostra glow mas ícone levemente escurecido
                    self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 90)), (sx, y0))
            elif skill.max_charges > 0:
                # Habilidade baseada em cargas (vitoria_iminente etc.)
                if skill.current_cooldown > 0:
                    # Em cooldown: overlay progressivo + contador (igual skills normais)
                    cd_ratio = skill.current_cooldown / max(0.001, skill.cooldown)
                    ov_h     = int(self._HB_H * cd_ratio)
                    self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 160)),
                                     (sx, y0), area=pygame.Rect(0, 0, self._HB_W, ov_h))
                    cd_surf = self.font_sm.render(f"{skill.current_cooldown:.1f}", True, (220, 200, 130))
                    self.screen.blit(cd_surf, (sx + self._HB_W // 2 - cd_surf.get_width() // 2,
                                               y0 + self._HB_H // 2 - cd_surf.get_height() // 2))
                elif is_procced:
                    if skill.charge_timeout > 0 and skill.charge_timer > 0:
                        timer_surf = self.font_sm.render(f"{skill.charge_timer:.0f}s", True, (100, 255, 120))
                        self.screen.blit(timer_surf, (sx + self._HB_W // 2 - timer_surf.get_width() // 2,
                                                      y0 + self._HB_H // 2 - timer_surf.get_height() // 2))
                else:
                    self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 160)), (sx, y0))
            elif not visual_ready:
                # Cooldown normal
                cd_ratio = skill.current_cooldown / max(0.001, skill.cooldown)
                ov_h     = int(self._HB_H * cd_ratio)
                self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 160)),
                                 (sx, y0), area=pygame.Rect(0, 0, self._HB_W, ov_h))
                cd_surf = self.font_sm.render(f"{skill.current_cooldown:.1f}", True, (220, 200, 130))
                self.screen.blit(cd_surf, (sx + self._HB_W // 2 - cd_surf.get_width() // 2,
                                           y0 + self._HB_H // 2 - cd_surf.get_height() // 2))

            # --- Lock de talento: skill requer talento não alocado ---
            _talent_locked = skill.skill_id and self._is_talent_locked(skill.skill_id)

            # --- Borda (por cima do overlay) ---
            if _talent_locked:
                # Borda roxa para indicar requisito de talento
                pygame.draw.rect(self.screen, (130, 50, 180), r, 2, border_radius=5)
            elif is_procced:
                br = int(200 + 55 * pulse)
                bg_ = int(150 + 60 * pulse)
                border = (br, bg_, 30)
                pygame.draw.rect(self.screen, border, r, 3, border_radius=5)
            else:
                border = (200, 160, 60) if visual_ready else (70, 55, 28)
                pygame.draw.rect(self.screen, border, r, 2, border_radius=5)

            # --- Overlay de channeling (Fatiador de Corpos) ---
            if channeling and getattr(skill, "skill_id", None) != "fatiador_de_corpos":
                ch_ratio = _char.fatiador_timer / 5.0
                ov_h = int(self._HB_H * ch_ratio)
                self.screen.blit(fill_surf((self._HB_W, self._HB_H), (80, 0, 0, 180)),
                                 (sx, y0), area=pygame.Rect(0, 0, self._HB_W, ov_h))

            # --- Overlay de GCD ---
            from components import PlayerSkills as _PS2
            _pskills = self.world.get_component(self.player_entity, _PS2)
            if not channeling and _pskills and _pskills.gcd_timer > 0:
                gcd_ratio = _pskills.gcd_timer / _PS2.GCD_DURATION
                ov_h = int(self._HB_H * gcd_ratio)
                self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 160)),
                                 (sx, y0), area=pygame.Rect(0, 0, self._HB_W, ov_h))

            # --- Overlay de Rage insuficiente ---
            # Usa custo modificado por talentos se disponível (ex: Veterano → golpe_poderoso_rage_cost)
            rage_cost = skill.rage_cost
            if skill.skill_id and _cs_hb:
                _talent_rage_hb = getattr(_cs_hb, f"{skill.skill_id}_rage_cost", None)
                if _talent_rage_hb is not None:
                    rage_cost = _talent_rage_hb
            # O overlay só é suprimido se o proc explicitamente dispensa o custo
            cost_bypassed = is_procced and skill.proc_ignores_cost
            if rage_cost > 0 and player_rage < rage_cost and not cost_bypassed:
                self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 140)), (sx, y0))

            # --- Overlay de talento removido (roxo) ---
            if _talent_locked:
                self.screen.blit(fill_surf((self._HB_W, self._HB_H), (80, 0, 120, 160)), (sx, y0))
                # Ícone de cadeado simples (X vermelho) no centro
                _lk_s = self.font_sm.render("✕", True, (220, 80, 220))
                self.screen.blit(_lk_s, (sx + self._HB_W // 2 - _lk_s.get_width() // 2,
                                         y0 + self._HB_H // 2 - _lk_s.get_height() // 2))

            # --- Overlay de drag de origem (dimming) ---
            if (_drag_hb.kind == "skill" and _drag_hb.source == "hotbar"
                    and _drag_hb.active and _drag_hb.source_idx == i):
                self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 140)), (sx, y0))

            # --- Pending-timeout: libera skill se servidor demorar demais ---
            # Ao expirar (rejeição), aplica mini-GCD local para não enviar nova req imediatamente.
            if getattr(skill, "_server_pending", False):
                t = getattr(skill, "_server_pending_timeout", 0.0) - self._dt
                skill._server_pending_timeout = max(0.0, t)
                if skill._server_pending_timeout <= 0.0:
                    skill._server_pending = False
                    # Garante GCD mesmo quando servidor rejeitou (sem SKILL_RESULT)
                    if player_skills and player_skills.gcd_timer <= 0:
                        player_skills.gcd_timer = PlayerSkills.GCD_DURATION * 0.5  # meio-GCD de segurança

            # --- Flash de falha / botão pressionado (aguardando confirmação) ---
            if skill.fail_flash_timer > 0:
                skill.fail_flash_timer = max(0.0, skill.fail_flash_timer - self._dt)
                self.screen.blit(fill_surf((self._HB_W, self._HB_H), (0, 0, 0, 50)), (sx, y0))  # ~20% escurecimento

            # --- Etiqueta da tecla (keybind configurável) ---
            kb_name = pygame.key.name(player_skills.keybinds[i]).upper()
            key_col = (220, 200, 140) if visual_ready else (80, 70, 50)
            if _talent_locked:
                key_col = (140, 60, 160)
            self.screen.blit(self.font_sm.render(kb_name, True, key_col), (sx + 3, y0 + 2))

            # --- Tooltip no hover ---
            _hb_self_drag_active = (_drag_hb.kind == "skill" and _drag_hb.source == "hotbar"
                                    and _drag_hb.active)
            if r.collidepoint(mx, my) and not _hb_self_drag_active:
                lines = self._skill_tooltip_lines(
                    skill, is_procced, visual_ready, target_hp_ratio, player_rage)
                # Requer talento? Injeta linha no topo
                if _talent_locked:
                    _tid_lk, _tname_lk, _mpts_lk = _TALENT_SKILL_REQS[skill.skill_id]
                    lines.insert(0, (f"⚠ Requer talento: {_tname_lk}", (200, 80, 220)))
                    lines.insert(1, ("", (100, 100, 100)))
                self._pending_skill_tooltip = (mx, y0 - 4, skill.name, lines)

        # --- Ghost icon: segue o mouse durante drag ativo ---
        if (_drag_hb.kind == "skill" and _drag_hb.source == "hotbar"
                and _drag_hb.active and _drag_hb.source_idx != -1):
            _gi    = _drag_hb.source_idx
            _gsk   = player_skills.skills[_gi]
            if _gsk is not None:
                _GSZ = self._HB_ICO
                _gkey = (ICONS.skill_key_by_name(_gsk.icon_name)
                          if _gsk.icon_name else ICONS.skill_key(_gi))
                _gic  = ICONS.get(_gkey, _GSZ)
                if _gic:
                    ghost = pygame.Surface((_GSZ, _GSZ), pygame.SRCALPHA)
                    ghost.blit(_gic, (0, 0))
                    ghost.set_alpha(180)
                    self.screen.blit(ghost, (mx - _GSZ // 2, my - _GSZ // 2))
                # Hint de remoção durante Shift+drag
                if _drag_hb.shift:
                    _hint = self.font_xs.render("Soltar fora → remover", True, (220, 80, 220))
                    self.screen.blit(_hint, (mx - _hint.get_width() // 2, my - _GSZ // 2 - 14))
