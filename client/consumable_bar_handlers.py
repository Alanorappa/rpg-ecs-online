"""
consumable_bar_handlers.py — Mixin com o desenho da barra de
consumíveis: slots, ícones, cooldown/GCD compartilhado com a hotbar
de skills, contagem de stack, atalhos de teclado e drag-and-drop com
remoção (igual à hotbar de habilidades). Separado de game.py para
manter GameEngine conciso. Esta classe NÃO deve ser instanciada
diretamente — ela é herdada por GameEngine, que fornece self.world,
self.player_entity, self.screen, self.font_xs e os demais atributos
referenciados aqui.
"""
import pygame

from engine.components import PlayerSkills
from ui.icon_manager import ICONS
from ui.ui_helpers import draw_stack_count, fill_surf


class ConsumableBarHandlers:

    # ------------------------------------------------------------------
    # Barra de consumíveis
    # ------------------------------------------------------------------

    def _draw_consumable_bar(self) -> None:
        from engine.components import ConsumableBar as _CB, Inventory as _Inv
        cbar = self.world.get_component(self.player_entity, _CB)
        inv  = self.world.get_component(self.player_entity, _Inv)
        if not cbar:
            return
        self._set_hotbar_row_scale()

        W   = self._HB_W
        H   = self._HB_H
        PAD = self._HB_PAD

        # Âncora à direita da skills bar compactada
        player_skills = self.world.get_component(self.player_entity, PlayerSkills)
        n_skills_occ  = sum(1 for s in player_skills.skills if s) if player_skills else 0
        skills_w      = n_skills_occ * W + max(0, n_skills_occ - 1) * PAD
        skills_x0     = self.screen.get_width() // 2 - skills_w // 2

        _drag_cb      = self._get_drag()
        dragging_cons = (_drag_cb.kind == "consumable" and _drag_cb.source == "inventory"
                         and _drag_cb.payload is not None)
        events_cb     = self._ui_events
        _mods_cb      = pygame.key.get_mods()
        _shift_cb     = bool(_mods_cb & pygame.KMOD_SHIFT)
        mx_cb, my_cb  = pygame.mouse.get_pos()

        # === Shift+drag para remover slot (mesmo padrão da hotbar de skills) ===

        # Pré-calcula posições de todos os slots para uso no drag
        _cb_all_occ = [(i, cbar.slots[i]) for i in range(_CB.NUM_SLOTS)]
        _cb_full_w  = _CB.NUM_SLOTS * W + (_CB.NUM_SLOTS - 1) * PAD

        # MOUSEDOWN → registra início de drag pendente
        for _ev_cb in events_cb:
            if _ev_cb.type == pygame.MOUSEBUTTONDOWN and _ev_cb.button == 1:
                if not dragging_cons:
                    _cb_x0_tmp = skills_x0 + skills_w + 20
                    _cb_y0_tmp = self.screen.get_height() - H - self._u(10)
                    for _jj_cb, (_ii_cb, _nm_cb) in enumerate(
                            [(i, cbar.slots[i]) for i in range(_CB.NUM_SLOTS) if cbar.slots[i]]):
                        _r_cb = pygame.Rect(_cb_x0_tmp + _jj_cb * (W + PAD), _cb_y0_tmp, W, H)
                        if _r_cb.collidepoint(_ev_cb.pos):
                            _drag_cb.kind       = "consumable"
                            _drag_cb.source     = "consumable_bar"
                            _drag_cb.source_idx = _ii_cb
                            _drag_cb.payload    = _nm_cb
                            _drag_cb.shift      = _shift_cb
                            _drag_cb.start_pos  = _ev_cb.pos
                            _drag_cb.active     = _shift_cb  # Shift → ativa imediatamente
                            break
                break

        # MOUSEMOTION → ativa drag ao superar threshold de 8px
        _cb_self_dragging = (_drag_cb.kind == "consumable" and _drag_cb.source == "consumable_bar"
                             and _drag_cb.source_idx != -1)
        if _cb_self_dragging and not _drag_cb.active and _drag_cb.start_pos is not None:
            _dx_cb = mx_cb - _drag_cb.start_pos[0]
            _dy_cb = my_cb - _drag_cb.start_pos[1]
            if _dx_cb * _dx_cb + _dy_cb * _dy_cb > 64:
                _drag_cb.active = True

        # MOUSEUP → confirma remoção ou reseta
        _cb_x0_base = skills_x0 + skills_w + 20
        _cb_y0_base = self.screen.get_height() - H - self._u(10)
        for _ev_cb in events_cb:
            if _ev_cb.type == pygame.MOUSEBUTTONUP and _ev_cb.button == 1:
                if _cb_self_dragging and _drag_cb.active and _drag_cb.shift:
                    # Verifica se soltou FORA da barra
                    _inside_cb = any(
                        pygame.Rect(_cb_x0_base + _jj3 * (W + PAD),
                                    _cb_y0_base, W, H).collidepoint(_ev_cb.pos)
                        for _jj3 in range(_CB.NUM_SLOTS)
                    )
                    if not _inside_cb:
                        cbar.slots[_drag_cb.source_idx] = None
                        self._save_config()
                # Reset
                if _cb_self_dragging:
                    _drag_cb.reset()
                break

        # Durante drag do inventário OU drag de remoção: mostra TODOS os slots
        _cb_drag_active_now = (_drag_cb.kind == "consumable" and _drag_cb.source == "consumable_bar"
                               and _drag_cb.active)
        if dragging_cons or _cb_drag_active_now:
            cons_occ = [(i, cbar.slots[i]) for i in range(_CB.NUM_SLOTS)]
        else:
            cons_occ = [(i, cbar.slots[i]) for i in range(_CB.NUM_SLOTS) if cbar.slots[i]]

        if not cons_occ:
            return
        cons_w = len(cons_occ) * W + (len(cons_occ) - 1) * PAD
        x0     = skills_x0 + skills_w + 20
        y0     = self.screen.get_height() - H - self._u(10)
        mx, my = pygame.mouse.get_pos()

        released_cb = any(e.type == pygame.MOUSEBUTTONUP and e.button == 1
                          for e in events_cb)

        for j, (i, item_name) in enumerate(cons_occ):
            sx = x0 + j * (W + PAD)
            r  = pygame.Rect(sx, y0, W, H)

            # Drop de drag de inventário sobre este slot
            if dragging_cons and released_cb and r.collidepoint(mx_cb, my_cb):
                cbar.slots[i] = _drag_cb.payload
                _drag_cb.reset()
                self._save_config()
                dragging_cons = False

            # Background — destaque durante drag
            _is_dragged_out = (_drag_cb.kind == "consumable" and _drag_cb.source == "consumable_bar"
                              and _drag_cb.active and _drag_cb.shift and i == _drag_cb.source_idx)
            if dragging_cons and r.collidepoint(mx_cb, my_cb):
                bg_col = (30, 65, 40)
            elif _is_dragged_out:
                bg_col = (65, 20, 20)
            else:
                bg_col = (18, 36, 22)
            pygame.draw.rect(self.screen, bg_col, r, border_radius=5)

            if item_name:
                _ic_key = "item_" + item_name.lower().replace(" ", "_")
                item = next((it for it in inv.items if it.name == item_name), None) if inv else None
                _ic = ICONS.get(_ic_key, W - 2)
                if item:
                    # Item disponível — ícone normal
                    if _ic:
                        self.screen.blit(_ic, (r.x + 2, r.y + 2))
                    else:
                        letter = self.font_sm.render(item_name[0].upper(), False, (100, 220, 140))
                        self.screen.blit(letter, letter.get_rect(center=r.center))
                    draw_stack_count(self.screen, item, r, self.font_xs)
                else:
                    # Item esgotado — ícone com overlay escuro (igual skill indisponível)
                    if _ic:
                        self.screen.blit(_ic, (r.x + 2, r.y + 2))
                        self.screen.blit(fill_surf((W - 2, H - 2), (0, 0, 0, 160)), (r.x + 2, r.y + 2))
                    else:
                        letter = self.font_sm.render(item_name[0].upper(), False, (100, 140, 120))
                        self.screen.blit(letter, letter.get_rect(center=r.center))
                    # "0" no canto
                    zero_s = self.font_xs.render("0", False, (200, 80, 80))
                    self.screen.blit(zero_s, (r.right - zero_s.get_width() - 3,
                                              r.bottom - zero_s.get_height() - 1))

            # GCD overlay
            if cbar.global_cooldown > 0:
                gcd_ratio = cbar.global_cooldown / _CB.GCD_DURATION
                ov_h = int(H * gcd_ratio)
                # area=: 1 entrada de cache p/ qualquer ov_h (sem churn por altura)
                self.screen.blit(fill_surf((W, H), (0, 0, 0, 160)),
                                 (sx, y0), area=pygame.Rect(0, 0, W, ov_h))
                if j == 0:
                    cd_s = self.font_xs.render(f"{cbar.global_cooldown:.1f}", False, (160, 210, 170))
                    self.screen.blit(cd_s, cd_s.get_rect(
                        centerx=sx + W // 2, y=y0 + H // 2 - cd_s.get_height() // 2))

            # Border
            if dragging_cons and r.collidepoint(mx_cb, my_cb):
                border_col = (100, 220, 130)
            elif _is_dragged_out:
                border_col = (220, 80, 80)
            else:
                border_col = (80, 160, 100) if item_name else (40, 70, 50)
            pygame.draw.rect(self.screen, border_col, r, 2, border_radius=5)

            # Keybind label
            kb_name = pygame.key.name(cbar.keybinds[i]).upper()
            key_col = (140, 210, 160) if item_name else (50, 80, 60)
            self.screen.blit(self.font_sm.render(kb_name, False, key_col), (sx + 3, y0 + 2))

            # Tooltip (só quando não está em drag)
            _cb_self_drag_active = (_drag_cb.kind == "consumable" and _drag_cb.source == "consumable_bar"
                                    and _drag_cb.active)
            if not dragging_cons and not _cb_self_drag_active \
                    and r.collidepoint(mx_cb, my_cb) and item_name:
                item = next((it for it in inv.items if it.name == item_name), None) if inv else None
                if item and item.consumable:
                    lines = []
                    h_inst = item.consumable.get("heal_instant", 0)
                    h_tick = item.consumable.get("heal_per_tick", 0)
                    m_inst = item.consumable.get("mana_restore", 0)
                    m_tick = item.consumable.get("mana_per_tick", 0)
                    ticks  = item.consumable.get("ticks", 0)
                    interv = item.consumable.get("interval", 2.0)
                    ooc    = item.consumable.get("ooc_only", False)
                    if h_inst:
                        lines.append((f"Cura: +{h_inst} HP instantâneo", (80, 220, 120)))
                    if h_tick and ticks:
                        lines.append((f"Regen: +{h_tick} HP a cada {interv:.0f}s ({ticks}x)", (80, 200, 140)))
                    if m_inst:
                        lines.append((f"Mana: +{m_inst} instantâneo", (100, 180, 255)))
                    if m_tick and ticks:
                        total_mana = m_tick * ticks
                        lines.append((f"Mana: +{m_tick} a cada {interv:.0f}s ({total_mana} total)", (100, 160, 230)))
                    if ooc:
                        lines.append(("Apenas fora de combate", (220, 160, 60)))
                    _qty = item.stack if item else 0
                    lines.append((f"Quantidade: {_qty}", (160, 160, 160)))
                    self._pending_skill_tooltip = (mx_cb, y0 - 4, item_name, lines)

        # Cancel drag se botão liberado fora da barra
        if dragging_cons and released_cb:
            _drag_cb.reset()

        # Ghost do drag de inventário: ícone segue o mouse
        if _drag_cb.kind == "consumable" and _drag_cb.source == "inventory" and _drag_cb.payload:
            GSZ    = W
            _gc_k  = "item_" + _drag_cb.payload.lower().replace(" ", "_")
            _gc_ic = ICONS.get(_gc_k, GSZ - 4)
            ghost  = pygame.Surface((GSZ, GSZ), pygame.SRCALPHA)
            ghost.fill((20, 50, 30, 180))
            if _gc_ic:
                ghost.blit(_gc_ic, (2, 2))
            ghost.blit(fill_surf((GSZ, GSZ), (255, 255, 255, 120)), (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            self.screen.blit(ghost, (mx_cb - GSZ // 2, my_cb - GSZ // 2))

        # Ghost do Shift+drag do consumable bar: ícone semi-transparente segue o mouse
        if (_drag_cb.kind == "consumable" and _drag_cb.source == "consumable_bar"
                and _drag_cb.active and _drag_cb.source_idx != -1):
            _cg_name = cbar.slots[_drag_cb.source_idx]
            if _cg_name:
                _CGZ  = W
                _cg_k = "item_" + _cg_name.lower().replace(" ", "_")
                _cg_ic = ICONS.get(_cg_k, _CGZ - 4)
                _cg_ghost = pygame.Surface((_CGZ, _CGZ), pygame.SRCALPHA)
                if _cg_ic:
                    _cg_ghost.blit(_cg_ic, (0, 0))
                _cg_ghost.set_alpha(180)
                self.screen.blit(_cg_ghost, (mx_cb - _CGZ // 2, my_cb - _CGZ // 2))
                # Hint de remoção durante Shift+drag (igual à hotbar de skills)
                if _drag_cb.shift:
                    _cb_hint = self.font_xs.render("Soltar fora → remover", False, (220, 80, 220))
                    self.screen.blit(_cb_hint, (mx_cb - _cb_hint.get_width() // 2,
                                                my_cb - _CGZ // 2 - 14))
