"""
inventory_handlers.py — Mixin com o painel de equipamentos/inventário
(tecla I): equipar/desequipar itens, clique em slots, uso de consumíveis
e desenho do painel. Separado de game.py para manter GameEngine conciso.
Esta classe NÃO deve ser instanciada diretamente — ela é herdada por
GameEngine, que fornece self.world, self._my_eid, self._net e os demais
atributos referenciados aqui. As constantes de geometria do painel
(_PANEL_W, _RARITY_COLORS etc.) e _panel_origin() pertencem a esta
classe — eram as únicas usuárias quando ainda viviam em GameEngine.
"""
import pygame

from components import CharacterStats, CombatStats, Equipment, Inventory, Position, Wallet
from combat_log import LOG
from icon_manager import ICONS
from sound_manager import SOUNDS
from stat_fns import add_modifier, learn_recipe, remove_modifier
from ui_helpers import draw_stack_count, item_tooltip_lines


class InventoryHandlers:

    _RARITY_COLORS = {
        "common":   (200, 200, 200),
        "uncommon": ( 30, 200,  30),
        "rare":     ( 80, 140, 255),
        "epic":     (180,  50, 255),
    }

    # Constantes do painel de inventário (usadas por draw E click)
    _PANEL_W    = 720
    _PANEL_H    = 660
    _PAD        = 10
    _HEADER_H   = 28
    _EQ_W       = 230   # largura da coluna de equipamento
    _EQ_SLOT_H  = 36    # altura de cada slot equipado
    _EQ_ICON    = 28    # ícone dentro do slot de equip
    _BODY_H     = 360   # 10 slots × 36px
    _INV_SLOT   = 60    # tamanho do slot de inventário (quadrado)
    _INV_COLS   = 5     # colunas na grade de inventário
    _INV_GAP    = 4     # espaço entre slots

    def _panel_origin(self):
        x0 = self.screen.get_width()  // 2 - self._PANEL_W // 2
        y0 = self.screen.get_height() // 2 - self._PANEL_H // 2
        return x0, y0
    # ------------------------------------------------------------------ #
    #  Equip / Unequip
    # ------------------------------------------------------------------ #

    def _equip_item(self, item):
        inv          = self.world.get_component(self.player_entity, Inventory)
        equip        = self.world.get_component(self.player_entity, Equipment)
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        if not (inv and equip and combat_stats):
            return

        target_slot = item.slot
        if target_slot not in equip.slots:
            return

        # Restrição de armor_class por classe do personagem
        if getattr(item, "armor_class", "") and item.item_type == "armor":
            from stats_system import CLASS_ARMOR_ALLOWED
            char = self.world.get_component(self.player_entity, CharacterStats)
            allowed = CLASS_ARMOR_ALLOWED.get(char.class_id if char else "", frozenset())
            if item.armor_class not in allowed:
                _names = {"placa": "Placa", "couro": "Couro", "tecido": "Tecido"}
                LOG.add(f"Sua classe não pode usar armadura de {_names.get(item.armor_class, item.armor_class)}.", (255, 100, 80))
                return

        # Arma de duas mãos → desequipa offhand se houver
        if getattr(item, 'two_handed', False) and target_slot == "mainhand":
            old_oh = equip.slots.get("offhand")
            if old_oh:
                for mod in old_oh.modifiers:
                    remove_modifier(combat_stats, mod)
                inv.items.append(old_oh)
                equip.slots["offhand"] = None

        # Offhand bloqueado por arma de duas mãos
        if target_slot == "offhand" and equip.is_offhand_locked():
            return

        # Troca com item já equipado
        old_item = equip.slots[target_slot]
        if old_item:
            for mod in old_item.modifiers:
                remove_modifier(combat_stats, mod)
            inv.items.append(old_item)

        # Equipa o novo item
        equip.slots[target_slot] = item
        inv.items.remove(item)
        if target_slot == "mainhand" and getattr(item, "attack_speed", 0.0) > 0:
            combat_stats.base_attack_interval = item.attack_speed
        for mod in item.modifiers:
            add_modifier(combat_stats, mod)

    def _unequip_slot(self, slot_name: str):
        inv          = self.world.get_component(self.player_entity, Inventory)
        equip        = self.world.get_component(self.player_entity, Equipment)
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        if not (inv and equip and combat_stats):
            return

        item = equip.slots.get(slot_name)
        if item is None:
            return
        if len(inv.items) >= inv.max_slots:
            return  # inventário cheio

        if slot_name == "mainhand":
            combat_stats.base_attack_interval = combat_stats._default_attack_interval
        for mod in item.modifiers:
            remove_modifier(combat_stats, mod)
        equip.slots[slot_name] = None
        inv.items.append(item)

    def _handle_inventory_click(self, event):
        if event.button not in (1, 3):
            return
        mx, my = event.pos
        x0, y0 = self._panel_origin()
        body_y  = y0 + self._PAD + self._HEADER_H

        # --- Botão fechar ---
        if event.button == 1:
            close_r = pygame.Rect(x0 + self._PANEL_W - 36, y0 + 4, 32, 32)
            if close_r.collidepoint(mx, my):
                self._show_inventory = False
                self._selected_inv_idx = -1
                return

        # --- Grade de inventário (esquerdo = selecionar, direito = equipar) ---
        inv = self.world.get_component(self.player_entity, Inventory)
        if inv:
            gx = x0 + self._EQ_W + self._PAD * 3
            step = self._INV_SLOT + self._INV_GAP
            for i, item in enumerate(inv.items):
                col = i % self._INV_COLS
                row = i // self._INV_COLS
                r = pygame.Rect(gx + col * step, body_y + row * step,
                                self._INV_SLOT, self._INV_SLOT)
                if r.collidepoint(mx, my):
                    if event.button == 3:
                        if getattr(item, "consumable", None):
                            self._use_consumable(item, i, inv)
                        else:
                            self._equip_item(item)
                        self._selected_inv_idx = -1
                    else:
                        self._selected_inv_idx = i if self._selected_inv_idx != i else -1
                    return
            # Clique fora dos slots da grade deseleciona
            if event.button == 1:
                self._selected_inv_idx = -1

        # --- Coluna de equipamentos (qualquer clique = desequipar) ---
        equip = self.world.get_component(self.player_entity, Equipment)
        if equip:
            for i, slot_name in enumerate(Equipment.SLOT_LABELS):
                r = pygame.Rect(x0 + self._PAD,
                                body_y + i * self._EQ_SLOT_H,
                                self._EQ_W - 2, self._EQ_SLOT_H - 2)
                if r.collidepoint(mx, my) and equip.slots[slot_name] is not None:
                    self._unequip_slot(slot_name)
                    return

    def _use_consumable(self, item, idx: int, inv) -> None:
        """Usa um item consumível do inventário."""
        from components import CombatStats, CombatState, ActiveRegen, ConsumableBar as _CB
        from combat_log import LOG
        from floating_text import FLT

        # Modo online: delega para ConsumableSystem (que notifica o servidor)
        if getattr(self, "_net", None) and self._consumable_system:
            cbar = self.world.get_component(self.player_entity, _CB)
            self._consumable_system._use_consumable(
                self.player_entity, item.name, cbar
            )
            return

        cs     = self.world.get_component(self.player_entity, CombatStats)
        state  = self.world.get_component(self.player_entity, CombatState)
        pos_c  = self.world.get_component(self.player_entity, Position)
        if not cs:
            return

        c = item.consumable

        # Receita: aprende e remove da bag imediatamente
        if "learn_recipe" in c:
            from components import LearnedRecipes
            lr = self.world.get_component(self.player_entity, LearnedRecipes)
            if lr:
                recipe_id = c["learn_recipe"]
                if learn_recipe(lr, recipe_id):
                    from crafting_data import RECIPES
                    rname = RECIPES.get(recipe_id, {}).get("name", recipe_id)
                    LOG.add(f"Receita aprendida: {rname}!", (220, 180, 50))
                    SOUNDS.play_ui("levelup")
                else:
                    LOG.add("Voce ja conhece essa receita.", (160, 140, 80))
            item.stack -= 1
            if item.stack <= 0:
                inv.items.pop(idx)
            return

        ooc_only = c.get("ooc_only", False)
        if ooc_only and state and state.in_combat:
            LOG.add("Não pode usar em combate!", (220, 100, 60))
            return

        from components import CharacterStats as _CHScons
        _char_cons = self.world.get_component(self.player_entity, _CHScons)

        # Cura instantânea de HP
        heal_now = c.get("heal_instant", 0)
        if heal_now:
            actual = min(heal_now, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + heal_now)
            if pos_c and actual > 0:
                FLT.add(f"+{actual}", pos_c.x, pos_c.y - 16, (80, 220, 120), "normal",
                        self.player_entity)
            LOG.add(f"Usou {item.name}: +{actual} HP", (80, 220, 120))

        # Restauração instantânea de mana
        mana_now = c.get("mana_restore", 0)
        if mana_now and _char_cons and _char_cons.max_mana > 0:
            actual_m = min(mana_now, _char_cons.max_mana - _char_cons.mana)
            _char_cons.mana = min(_char_cons.max_mana, _char_cons.mana + mana_now)
            cs.mana = _char_cons.mana
            if pos_c and actual_m > 0:
                FLT.add(f"+{actual_m} MP", pos_c.x, pos_c.y - 28, (100, 180, 255), "normal",
                        self.player_entity)
            LOG.add(f"Usou {item.name}: +{actual_m} mana", (100, 180, 255))

        # HoT de HP
        ticks = c.get("ticks", 0)
        if ticks and c.get("heal_per_tick", 0):
            existing = self.world.get_component(self.player_entity, ActiveRegen)
            if existing:
                self.world.remove_component(self.player_entity, ActiveRegen)
            self.world.add_component(
                self.player_entity,
                ActiveRegen(c["heal_per_tick"], c["interval"], ticks),
            )
            total = c["heal_per_tick"] * ticks
            LOG.add(f"Usou {item.name}: recupera {total} HP ao longo do tempo", (80, 220, 120))

        # HoT de mana
        from components import ActiveManaRegen as _AMRcons
        if ticks and c.get("mana_per_tick", 0) and _char_cons and _char_cons.max_mana > 0:
            try:
                self.world.remove_component(self.player_entity, _AMRcons)
            except Exception:
                pass
            self.world.add_component(
                self.player_entity,
                _AMRcons(c["mana_per_tick"], c["interval"], ticks),
            )
            total_m = c["mana_per_tick"] * ticks
            LOG.add(f"Usou {item.name}: recupera {total_m} mana ao longo do tempo", (100, 180, 255))

        # Decrementa stack; remove o slot apenas quando esgotado
        item.stack -= 1
        if item.stack <= 0:
            inv.items.pop(idx)

    def _draw_inventory_panel(self):
        inv          = self.world.get_component(self.player_entity, Inventory)
        equip        = self.world.get_component(self.player_entity, Equipment)
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        if not (inv and equip and combat_stats):
            return

        x0, y0  = self._panel_origin()
        W, H    = self._PANEL_W, self._PANEL_H
        PAD     = self._PAD
        mx, my  = pygame.mouse.get_pos()

        _inv_ev = self._ui_events
        _clicked_inv  = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in _inv_ev)
        _released_inv = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in _inv_ev)
        # Cancelar drag ao soltar fora do inventário
        if _released_inv and self._inv_drag_item:
            pass  # será cancelado em _draw_consumable_bar se não cair em slot

        # ---- Fundo ----
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((15, 10, 5, 220))
        self.screen.blit(overlay, (x0, y0))
        pygame.draw.rect(self.screen, (140, 100, 60), (x0, y0, W, H), 2, border_radius=4)

        title = self.font_md.render("Equipamentos", True, (200, 170, 100))
        self.screen.blit(title, (x0 + PAD, y0 + 4))

        # Botão X (fechar)
        close_r = pygame.Rect(x0 + W - 36, y0 + 4, 32, 32)
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (180, 60, 60) if close_hov else (100, 35, 35),
                         close_r, border_radius=3)
        xs = self.font_md.render("X", True, (255, 255, 255))
        self.screen.blit(xs, (close_r.centerx - xs.get_width() // 2,
                               close_r.centery - xs.get_height() // 2))

        body_y    = y0 + PAD + self._HEADER_H
        divider_y = body_y + self._BODY_H + PAD
        col_x     = x0 + self._EQ_W + PAD * 3

        pygame.draw.line(self.screen, (90, 70, 40), (x0 + PAD, divider_y), (x0 + W - PAD, divider_y))
        pygame.draw.line(self.screen, (90, 70, 40), (col_x - PAD, y0 + PAD), (col_x - PAD, divider_y))

        # ---- Cabeçalhos ----
        hdr = (160, 130, 80)
        self.screen.blit(self.font_sm.render("Equipado  (clique p/ desequipar)", True, hdr), (x0 + PAD, y0 + PAD + 2))
        bag_hint = "  [DEL] deletar selecionado" if self._selected_inv_idx >= 0 else "  clique esq. p/ selecionar | dir. p/ equipar"
        self.screen.blit(self.font_sm.render(f"Mochila ({len(inv.items)}/{inv.max_slots}){bag_hint}", True, hdr), (col_x, y0 + PAD + 2))

        # ---- Coluna de equipamentos (ícone + label + nome) ----
        for i, (slot_name, label) in enumerate(Equipment.SLOT_LABELS.items()):
            ry     = body_y + i * self._EQ_SLOT_H
            r      = pygame.Rect(x0 + PAD, ry, self._EQ_W - 2, self._EQ_SLOT_H - 2)
            item   = equip.slots[slot_name]
            locked = (slot_name == "offhand" and equip.is_offhand_locked())

            hovered = r.collidepoint(mx, my)
            bg     = (50, 20, 20) if locked else ((55, 42, 18) if hovered else (32, 22, 12))
            border = (100, 40, 40) if locked else ((180, 140, 60) if hovered else (70, 50, 30))
            pygame.draw.rect(self.screen, bg, r, border_radius=3)
            pygame.draw.rect(self.screen, border, r, 1, border_radius=3)

            # ícone (quadrado _EQ_ICON × _EQ_ICON)
            ic = self._EQ_ICON
            icon_r = pygame.Rect(r.x + 3, r.y + (self._EQ_SLOT_H - 2 - ic) // 2, ic, ic)
            if item:
                icon_surf = ICONS.get(ICONS.item_key(item), ic)
                if icon_surf:
                    self.screen.blit(icon_surf, icon_r)
                else:
                    fb_color = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
                    pygame.draw.rect(self.screen, fb_color, icon_r, border_radius=2)
            else:
                pygame.draw.rect(self.screen, (45, 35, 20) if not locked else (60, 20, 20), icon_r, border_radius=2)
                pygame.draw.rect(self.screen, border, icon_r, 1, border_radius=2)

            # Label do slot
            lbl_surf = self.font_sm.render(f"{label}", True, (120, 100, 70))
            self.screen.blit(lbl_surf, (icon_r.right + 4, r.y + 3))

            # Nome do item (linha 2)
            if item:
                col_name = self._RARITY_COLORS.get(item.rarity, (200, 200, 200))
                self.screen.blit(self.font_sm.render(item.name, True, col_name), (icon_r.right + 4, r.y + 18))
            elif locked:
                self.screen.blit(self.font_sm.render("(2 maos)", True, (100, 60, 60)), (icon_r.right + 4, r.y + 18))

            # Tooltip no hover
            if hovered and item:
                lines = item_tooltip_lines(item)
                lines.append(("Clique p/ desequipar", (140, 140, 140)))
                self._pending_tooltip = (mx, my, item.name, lines)

        # ---- Grade de inventário (ícones) ----
        step  = self._INV_SLOT + self._INV_GAP
        total = inv.max_slots
        for i in range(total):
            col_i = i % self._INV_COLS
            row_i = i // self._INV_COLS
            sx = col_x + col_i * step
            sy = body_y + row_i * step
            r  = pygame.Rect(sx, sy, self._INV_SLOT, self._INV_SLOT)
            item = inv.items[i] if i < len(inv.items) else None

            hovered  = r.collidepoint(mx, my)
            selected = (i == self._selected_inv_idx)
            bg     = (55, 42, 18) if hovered else (30, 22, 12)
            if selected:
                border = (220, 60, 60)
            elif hovered and item:
                border = (180, 140, 60)
            else:
                border = (65, 48, 28)
            pygame.draw.rect(self.screen, bg, r, border_radius=3)
            pygame.draw.rect(self.screen, border, r, 2 if selected else 1, border_radius=3)

            if item:
                ic        = self._INV_SLOT - 8
                icon_r    = pygame.Rect(sx + 4, sy + 4, ic, ic)
                icon_surf = ICONS.get(ICONS.item_key(item), ic)
                if icon_surf:
                    self.screen.blit(icon_surf, icon_r)
                else:
                    fb = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
                    pygame.draw.rect(self.screen, fb, icon_r, border_radius=2)
                # Ponto de raridade (canto inferior direito) — só em não-empilháveis
                if getattr(item, "max_stack", 1) <= 1:
                    dot_col = self._RARITY_COLORS.get(item.rarity, (150, 150, 150))
                    pygame.draw.circle(self.screen, dot_col, (r.right - 5, r.bottom - 5), 4)

                # Contador de stack (canto inferior direito)
                draw_stack_count(self.screen, item, r, self.font_sm)

                if hovered:
                    del_hint = "DEL p/ deletar | " if selected else ""
                    lines = item_tooltip_lines(item)
                    is_consumable = getattr(item, "consumable", None)
                    if is_consumable:
                        lines.append((f"{del_hint}Arraste p/ barra de consumíveis | Dir. p/ usar", (140, 140, 140)))
                        self._pending_tooltip = (mx, my, item.name, lines)
                        # Iniciar drag ao clicar com botão esquerdo
                        if _clicked_inv and r.collidepoint(mx, my):
                            self._inv_drag_item = item.name
                    else:
                        lines.append((f"{del_hint}Clique dir. p/ equipar | Shift p/ comparar", (140, 140, 140)))
                        self._pending_tooltip = (mx, my, item.name, lines,
                                                 item, equip.slots.get(item.slot))

        # ---- Seção de estatísticas (2 colunas) ----
        sy2   = divider_y + PAD
        half  = W // 2
        cL    = x0 + PAD          # coluna esquerda: rótulo
        cLv   = x0 + 130          # coluna esquerda: valor
        cR    = x0 + half + PAD   # coluna direita: rótulo
        cRv   = x0 + half + 130   # coluna direita: valor
        HDR   = (160, 140, 100)
        VAL   = (255, 220, 120)
        ROW   = 20                 # altura de linha

        self.screen.blit(self.font_sm.render("── Estatísticas ──", True, (180, 150, 90)), (x0 + PAD, sy2))
        sy2 += ROW

        char_stats = self.world.get_component(self.player_entity, CharacterStats)

        def sv(label, value, col_lbl, col_val, y, color=VAL):
            self.screen.blit(self.font_sm.render(label + ":", True, HDR), (col_lbl, y))
            self.screen.blit(self.font_sm.render(value,        True, color), (col_val, y))

        # Linha 1
        sv("HP",        f"{int(combat_stats.current_hp)}/{combat_stats.max_hp}", cL, cLv, sy2)
        sv("Acerto",    f"{combat_stats.acerto:.1f}%", cR, cRv, sy2)
        sy2 += ROW

        # Linha 2
        sv("Atq. Físico", f"{int(combat_stats.attack_power)}", cL, cLv, sy2)
        sv("Esquiva",   f"{combat_stats.dodge_rating / 20:.1f}%", cR, cRv, sy2)
        sy2 += ROW

        # Linha 3
        sv("Atq. Mágico", f"{int(combat_stats.spell_power)}", cL, cLv, sy2)
        sv("Aparo",     f"{combat_stats.parry_rating / 20:.1f}%", cR, cRv, sy2)
        sy2 += ROW

        # Linha 4
        sv("Armadura",  f"{int(combat_stats.armor)}", cL, cLv, sy2)
        sv("Vel. Ataque", f"{combat_stats.attack_interval:.2f}s", cR, cRv, sy2)
        sy2 += ROW

        # Linha 5
        sv("Estamina",  f"{int(combat_stats.stamina)}", cL, cLv, sy2)
        sv("Crítico",   f"{combat_stats.crit_rating * 100:.1f}%", cR, cRv, sy2)
        sy2 += ROW

        # Linha 6 — recurso da classe
        if char_stats:
            if char_stats.max_mana > 0:
                sv("Mana",  f"{int(char_stats.mana)}/{char_stats.max_mana}", cL, cLv, sy2)
            elif char_stats.max_concentration > 0:
                sv("Concentração", f"{int(char_stats.concentration)}/{char_stats.max_concentration}", cL, cLv, sy2)
            else:
                sv("Raiva", f"{char_stats.rage}/{char_stats.max_rage}", cL, cLv, sy2)
        sy2 += ROW

        # ---- Rodapé: moedas (sempre no rodapé do painel) ----
        wallet = self.world.get_component(self.player_entity, Wallet)
        if wallet:
            footer_y = y0 + H - 34
            pygame.draw.line(self.screen, (90, 70, 40),
                             (x0 + PAD, footer_y - 4), (x0 + W - PAD, footer_y - 4))
            coin_x, coin_y = x0 + PAD + 10, footer_y + 12
            pygame.draw.circle(self.screen, (180, 140, 0), (coin_x, coin_y), 9)
            pygame.draw.circle(self.screen, (255, 215, 0), (coin_x, coin_y), 7)
            pygame.draw.circle(self.screen, (120, 90, 0),  (coin_x, coin_y), 9, 1)
            g_surf = self.font_sm.render("G", True, (120, 90, 0))
            self.screen.blit(g_surf, (coin_x - g_surf.get_width() // 2,
                                      coin_y - g_surf.get_height() // 2))
            gold_surf = self.font_md.render(f"{wallet.gold} moedas", True, (255, 215, 0))
            self.screen.blit(gold_surf, (x0 + PAD + 24, footer_y + 6))
