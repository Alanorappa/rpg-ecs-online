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

from engine.components import CharacterStats, CombatStats, Equipment, Inventory, Position, Wallet
from ui.combat_log import LOG
from ui.icon_manager import ICONS
from ui.sound_manager import SOUNDS
from engine.stat_fns import add_modifier, learn_recipe, remove_modifier
from ui.ui_helpers import draw_stack_count, item_tooltip_lines, fill_surf
from ui.ui_sizes import UI


class InventoryHandlers:

    _RARITY_COLORS = {
        "common":    (200, 200, 200),
        "uncommon":  ( 30, 200,  30),
        "rare":      ( 80, 140, 255),
        "epic":      (180,  50, 255),
        "legendary": (224, 135,  47),
        "mythic":    (221,  68,  68),
    }

    # Constantes do painel de inventário (usadas por draw E click)
    # Valores em ui_sizes.py (UI.INVENTORY_*) — único lugar pra ajustar.
    _PANEL_W    = UI.INVENTORY_W
    _PANEL_H    = UI.INVENTORY_H
    _PAD        = UI.INVENTORY_PAD
    _HEADER_H   = UI.INVENTORY_HEADER_H
    _EQ_W       = UI.INVENTORY_EQ_W       # largura da coluna de equipamento
    _EQ_SLOT_H  = UI.INVENTORY_EQ_SLOT_H  # altura de cada slot equipado
    _EQ_ICON    = UI.INVENTORY_EQ_ICON    # ícone dentro do slot de equip
    _BODY_H     = UI.INVENTORY_BODY_H     # 10 slots × 36px
    _INV_SLOT   = UI.INVENTORY_SLOT       # tamanho do slot de inventário (quadrado)
    _INV_COLS   = UI.INVENTORY_COLS       # colunas na grade de inventário
    _INV_GAP    = UI.INVENTORY_GAP        # espaço entre slots

    def _panel_origin(self):
        x0, y0 = self._safe_panel_origin(self._PANEL_W, self._PANEL_H)
        return x0 + UI.INVENTORY_OFFSET_X, y0 + UI.INVENTORY_OFFSET_Y
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

        char = self.world.get_component(self.player_entity, CharacterStats)

        # Restrição de armor_class por classe do personagem — feedback
        # imediato; servidor valida de novo em update_player_equipment
        # (autoritativo, fonte única de verdade — este check aqui é só UX).
        if getattr(item, "armor_class", "") and item.item_type == "armor":
            from engine.stats_system import CLASS_ARMOR_ALLOWED
            allowed = CLASS_ARMOR_ALLOWED.get(char.class_id if char else "", frozenset())
            if item.armor_class not in allowed:
                _names = {"placa": "Placa", "couro": "Couro", "tecido": "Tecido"}
                LOG.add(f"Sua classe não pode usar armadura de {_names.get(item.armor_class, item.armor_class)}.", (255, 100, 80))
                return

        # Restrição de arma/escudo/aljava por classe — mesmo racional do
        # armor_class acima (feedback imediato; servidor valida de novo).
        if item.item_type in ("weapon", "shield", "quiver"):
            from engine.stats_system import is_weapon_allowed_for_class
            if not is_weapon_allowed_for_class(item, char.class_id if char else ""):
                LOG.add(f"Sua classe não pode usar {item.name}.", (255, 100, 80))
                return

        # Nível requerido — mesmo racional: feedback imediato, servidor é
        # quem realmente bloqueia (EQUIP_REJECTED se este check aqui for
        # burlado, ex. cliente modificado).
        _lvl_req = getattr(item, "level_requirement", 1)
        if char is not None and char.level < _lvl_req:
            LOG.add(f"Requer nível {_lvl_req} (você está no nível {char.level}).", (255, 100, 80))
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
        self._send_equip_sync()

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
        self._send_equip_sync()

    def _handle_inventory_click(self, event):
        if event.button not in (1, 3):
            return
        mx, my = event.pos
        x0, y0 = self._panel_origin()
        body_y  = y0 + self._u(self._PAD) + self._u(self._HEADER_H)

        # --- Botão fechar ---
        if event.button == 1:
            close_r = pygame.Rect(x0 + self._u(self._PANEL_W) - self._u(36), y0 + self._u(4), self._u(32), self._u(32))
            if close_r.collidepoint(mx, my):
                self._show_inventory = False
                self._selected_inv_idx = -1
                return

        # --- Grade de inventário (esquerdo = selecionar, direito = equipar) ---
        inv = self.world.get_component(self.player_entity, Inventory)
        if inv:
            gx = x0 + self._u(self._EQ_W) + self._u(self._PAD) * 3
            step = self._u(self._INV_SLOT) + self._u(self._INV_GAP)
            for i, item in enumerate(inv.items):
                col = i % self._INV_COLS
                row = i // self._INV_COLS
                r = pygame.Rect(gx + col * step, body_y + row * step,
                                self._u(self._INV_SLOT), self._u(self._INV_SLOT))
                if r.collidepoint(mx, my):
                    if event.button == 3:
                        _trade_ui = self._get_trade_ui()
                        if _trade_ui is not None and _trade_ui.is_open:
                            self._offer_trade_item(i)
                        elif getattr(item, "consumable", None):
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
                r = pygame.Rect(x0 + self._u(self._PAD),
                                body_y + i * self._u(self._EQ_SLOT_H),
                                self._u(self._EQ_W) - self._u(2), self._u(self._EQ_SLOT_H) - self._u(2))
                if r.collidepoint(mx, my) and equip.slots[slot_name] is not None:
                    self._unequip_slot(slot_name)
                    return

    def _use_consumable(self, item, idx: int, inv) -> None:
        """Usa um item consumível do inventário."""
        from engine.components import CombatStats, CombatState, ActiveRegen, ConsumableBar as _CB
        from ui.combat_log import LOG
        from ui.floating_text import FLT

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
            from engine.components import LearnedRecipes
            lr = self.world.get_component(self.player_entity, LearnedRecipes)
            if lr:
                recipe_id = c["learn_recipe"]
                if learn_recipe(lr, recipe_id):
                    from content.crafting_data import RECIPES
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

        from engine.components import CharacterStats as _CHScons
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
        from engine.components import ActiveManaRegen as _AMRcons
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
        _char_inv    = self.world.get_component(self.player_entity, CharacterStats)
        _viewer_cls  = _char_inv.class_id if _char_inv else None

        x0, y0  = self._panel_origin()
        W, H    = self._u(self._PANEL_W), self._u(self._PANEL_H)
        PAD     = self._u(self._PAD)
        mx, my  = pygame.mouse.get_pos()

        _inv_ev = self._ui_events
        _clicked_inv  = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in _inv_ev)
        _released_inv = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in _inv_ev)
        # Cancelar drag ao soltar fora do inventário
        _drag_inv = self._get_drag()
        if (_released_inv and _drag_inv.kind == "consumable"
                and _drag_inv.source == "inventory" and _drag_inv.payload):
            pass  # será cancelado em _draw_consumable_bar se não cair em slot

        # ---- Fundo ----
        self.screen.blit(fill_surf((W, H), (15, 10, 5, 220)), (x0, y0))
        pygame.draw.rect(self.screen, (140, 100, 60), (x0, y0, W, H), 2, border_radius=4)

        title = self.font_md.render("Equipamentos", False, (200, 170, 100))
        title_y = y0 + self._u(4)
        self.screen.blit(title, (x0 + PAD, title_y))

        # Moedas — ao lado do título, topo do painel (longe do rodapé/hotbar)
        wallet = self.world.get_component(self.player_entity, Wallet)
        if wallet:
            coin_x, coin_y = x0 + W - self._u(150), title_y + title.get_height() // 2
            pygame.draw.circle(self.screen, (180, 140, 0), (coin_x, coin_y), self._u(8))
            pygame.draw.circle(self.screen, (255, 215, 0), (coin_x, coin_y), self._u(6))
            gold_surf = self.font_sm.render(f"{wallet.gold}", False, (255, 215, 0))
            self.screen.blit(gold_surf, (coin_x + self._u(12), coin_y - gold_surf.get_height() // 2))

        # Botão X (fechar)
        close_r = pygame.Rect(x0 + W - self._u(36), y0 + self._u(4), self._u(32), self._u(32))
        close_hov = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.screen, (180, 60, 60) if close_hov else (100, 35, 35),
                         close_r, border_radius=3)
        xs = self.font_md.render("X", False, (255, 255, 255))
        self.screen.blit(xs, (close_r.centerx - xs.get_width() // 2,
                               close_r.centery - xs.get_height() // 2))

        header_y  = title_y + title.get_height() + self._u(4)
        body_y    = header_y + self.font_sm.get_height() + self._u(6)
        divider_y = body_y + self._u(self._BODY_H) + PAD
        col_x     = x0 + self._u(self._EQ_W) + PAD * 3

        pygame.draw.line(self.screen, (90, 70, 40), (x0 + PAD, divider_y), (x0 + W - PAD, divider_y))
        pygame.draw.line(self.screen, (90, 70, 40), (col_x - PAD, header_y), (col_x - PAD, divider_y))

        # ---- Cabeçalhos (sem dicas redundantes — interações já aparecem no tooltip de cada item) ----
        hdr = (160, 130, 80)
        self.screen.blit(self.font_sm.render("Equipado", False, hdr), (x0 + PAD, header_y))
        bag_hint = "  [DEL] deletar selecionado" if self._selected_inv_idx >= 0 else ""
        self.screen.blit(self.font_sm.render(f"Mochila ({len(inv.items)}/{inv.max_slots}){bag_hint}", False, hdr), (col_x, header_y))

        # ---- Coluna de equipamentos (ícone + label + nome) ----
        eq_slot_h = self._u(self._EQ_SLOT_H)
        for i, (slot_name, label) in enumerate(Equipment.SLOT_LABELS.items()):
            ry     = body_y + i * eq_slot_h
            r      = pygame.Rect(x0 + PAD, ry, self._u(self._EQ_W) - self._u(2), eq_slot_h - self._u(2))
            item   = equip.slots[slot_name]
            locked = (slot_name == "offhand" and equip.is_offhand_locked())

            hovered = r.collidepoint(mx, my)
            bg     = (50, 20, 20) if locked else ((55, 42, 18) if hovered else (32, 22, 12))
            border = (100, 40, 40) if locked else ((180, 140, 60) if hovered else (70, 50, 30))
            pygame.draw.rect(self.screen, bg, r, border_radius=3)
            pygame.draw.rect(self.screen, border, r, 1, border_radius=3)

            # ícone (quadrado _EQ_ICON × _EQ_ICON)
            ic = self._u(self._EQ_ICON)
            icon_r = pygame.Rect(r.x + self._u(3), r.y + (eq_slot_h - self._u(2) - ic) // 2, ic, ic)
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
            lbl_surf = self.font_sm.render(f"{label}", False, (120, 100, 70))
            self.screen.blit(lbl_surf, (icon_r.right + self._u(4), r.y + self._u(3)))

            # Nome do item (linha 2)
            if item:
                col_name = self._RARITY_COLORS.get(item.rarity, (200, 200, 200))
                self.screen.blit(self.font_sm.render(item.name, False, col_name), (icon_r.right + self._u(4), r.y + self._u(18)))
            elif locked:
                self.screen.blit(self.font_sm.render("(2 maos)", False, (100, 60, 60)), (icon_r.right + self._u(4), r.y + self._u(18)))

            # Tooltip no hover
            if hovered and item:
                lines = item_tooltip_lines(item, _viewer_cls)
                lines.append(("Clique p/ desequipar", (140, 140, 140)))
                name_col = self._RARITY_COLORS.get(item.rarity, (255, 220, 100))
                self._pending_tooltip = (mx, my, item.name, lines, name_col)

        # ---- Grade de inventário (ícones) ----
        inv_slot = self._u(self._INV_SLOT)
        step  = inv_slot + self._u(self._INV_GAP)
        total = inv.max_slots
        for i in range(total):
            col_i = i % self._INV_COLS
            row_i = i // self._INV_COLS
            sx = col_x + col_i * step
            sy = body_y + row_i * step
            r  = pygame.Rect(sx, sy, inv_slot, inv_slot)
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
                ic        = inv_slot - self._u(8)
                icon_r    = pygame.Rect(sx + self._u(4), sy + self._u(4), ic, ic)
                icon_surf = ICONS.get(ICONS.item_key(item), ic)
                if icon_surf:
                    self.screen.blit(icon_surf, icon_r)
                else:
                    fb = self._RARITY_COLORS.get(item.rarity, (100, 100, 100))
                    pygame.draw.rect(self.screen, fb, icon_r, border_radius=2)
                # Ponto de raridade (canto inferior direito) — só em não-empilháveis
                if getattr(item, "max_stack", 1) <= 1:
                    dot_col = self._RARITY_COLORS.get(item.rarity, (150, 150, 150))
                    pygame.draw.circle(self.screen, dot_col, (r.right - self._u(5), r.bottom - self._u(5)), self._u(4))

                # Contador de stack (canto inferior direito)
                draw_stack_count(self.screen, item, r, self.font_sm)

                if hovered:
                    del_hint = "DEL p/ deletar | " if selected else ""
                    lines = item_tooltip_lines(item, _viewer_cls)
                    name_col = self._RARITY_COLORS.get(item.rarity, (255, 220, 100))
                    is_consumable = getattr(item, "consumable", None)
                    if is_consumable:
                        lines.append((f"{del_hint}Arraste p/ barra de consumíveis | Dir. p/ usar", (140, 140, 140)))
                        self._pending_tooltip = (mx, my, item.name, lines, name_col)
                        # Iniciar drag ao clicar com botão esquerdo
                        if _clicked_inv and r.collidepoint(mx, my):
                            _drag_inv.kind    = "consumable"
                            _drag_inv.source  = "inventory"
                            _drag_inv.payload = item.name
                            _drag_inv.active  = True
                    else:
                        lines.append((f"{del_hint}Clique dir. p/ equipar | Shift p/ comparar", (140, 140, 140)))
                        self._pending_tooltip = (mx, my, item.name, lines, name_col,
                                                 item, equip.slots.get(item.slot))

        # ---- Seção de estatísticas (2 colunas, cada uma com Base | Itens) ----
        # "Base" = atributo cru + talentos + buffs (tudo que NÃO é item
        # equipado); "Itens" = só a contribuição do equipamento, isolada via
        # CombatStats.equipment_bonus() (filtra Modifier.source=="equipment").
        # Base + Itens == o valor final usado em combate.
        sy2   = divider_y + PAD
        half  = W // 2
        cL    = x0 + PAD                  # coluna esquerda: rótulo
        cLb   = x0 + self._u(130)         # coluna esquerda: Base (mesmo offset do label->valor antigo)
        cLi   = x0 + self._u(200)         # coluna esquerda: Itens
        cR    = x0 + half + PAD           # coluna direita: rótulo
        cRb   = x0 + half + self._u(130)  # coluna direita: Base
        cRi   = x0 + half + self._u(200)  # coluna direita: Itens
        HDR   = (160, 140, 100)
        VAL   = (255, 220, 120)
        ITEM_COL  = (120, 200, 120)   # verde — bônus de equipamento
        ROW   = self._u(20)            # altura de linha

        self.screen.blit(self.font_sm.render("-- Estatísticas --", False, (180, 150, 90)), (x0 + PAD, sy2))
        hdr_base  = self.font_xs.render("Base", False, HDR)
        hdr_itens = self.font_xs.render("Itens", False, HDR)
        self.screen.blit(hdr_base,  (cLb, sy2 + self._u(2)))
        self.screen.blit(hdr_itens, (cLi, sy2 + self._u(2)))
        self.screen.blit(hdr_base,  (cRb, sy2 + self._u(2)))
        self.screen.blit(hdr_itens, (cRi, sy2 + self._u(2)))
        sy2 += ROW

        char_stats = self.world.get_component(self.player_entity, CharacterStats)

        def sv(label, total: float, item_bonus: float, col_lbl, col_base, col_item, y,
              fmt=lambda v: f"{int(round(v))}", item_fmt=None):
            """Desenha label + Base (total - item_bonus) + Itens (item_bonus,
            com sinal, ou '—' se não houver contribuição de equipamento)."""
            item_fmt = item_fmt or fmt
            base_val = total - item_bonus
            self.screen.blit(self.font_sm.render(label + ":", False, HDR), (col_lbl, y))
            self.screen.blit(self.font_sm.render(fmt(base_val), False, VAL), (col_base, y))
            if abs(item_bonus) >= 0.05:
                sign = "+" if item_bonus > 0 else ""
                txt = f"{sign}{item_fmt(item_bonus)}"
                self.screen.blit(self.font_sm.render(txt, False, ITEM_COL), (col_item, y))
            else:
                self.screen.blit(self.font_sm.render("—", False, (90, 80, 60)), (col_item, y))

        eq = combat_stats.equipment_bonus

        # Linha 1
        sv("HP", combat_stats.max_hp, eq("stamina") * 10, cL, cLb, cLi, sy2)
        sv("Acerto", combat_stats.acerto, eq("acerto"), cR, cRb, cRi, sy2,
           fmt=lambda v: f"{v:.1f}%")
        sy2 += ROW

        # Linha 2
        sv("Atq. Físico", combat_stats.attack_power, eq("attack_power"), cL, cLb, cLi, sy2)
        sv("Esquiva", combat_stats.dodge_rating / 20, eq("dodge_rating") / 20, cR, cRb, cRi, sy2,
           fmt=lambda v: f"{v:.1f}%")
        sy2 += ROW

        # Linha 3
        sv("Atq. Mágico", combat_stats.spell_power, eq("spell_power"), cL, cLb, cLi, sy2)
        sv("Aparo", combat_stats.parry_rating / 20, eq("parry_rating") / 20, cR, cRb, cRi, sy2,
           fmt=lambda v: f"{v:.1f}%")
        sy2 += ROW

        # Linha 4 — Vel. Ataque: item_bonus pode ser negativo (item mais rápido
        # reduz o intervalo) — fmt sem arredondar pro inteiro, é em segundos.
        sv("Armadura", combat_stats.armor, eq("armor"), cL, cLb, cLi, sy2)
        sv("Vel. Ataque", combat_stats.attack_interval, eq("attack_interval"), cR, cRb, cRi, sy2,
           fmt=lambda v: f"{v:.2f}s")
        sy2 += ROW

        # Linha 5
        sv("Estamina", combat_stats.stamina, eq("stamina") * 10, cL, cLb, cLi, sy2)
        sv("Crítico", combat_stats.crit_rating * 100, eq("crit_rating") * 100, cR, cRb, cRi, sy2,
           fmt=lambda v: f"{v:.1f}%")
        sy2 += ROW

        # Linha 6 — recurso da classe (mesmo critério do HUD: class_id direto,
        # não "max_X > 0" — um valor de recurso de outra classe ficando > 0
        # por engano não troca o rótulo errado, ver PROBLEMAS_ARQUITETURA.md).
        # Equipamento nunca modifica mana/concentração/raiva hoje — "Itens"
        # sempre fica "—" aqui, mas a linha continua mostrando o total certo.
        if char_stats:
            if char_stats.class_id == "mago":
                sv("Mana",  char_stats.max_mana, 0, cL, cLb, cLi, sy2)
            elif char_stats.class_id == "arqueiro":
                sv("Concentração", char_stats.max_concentration, 0, cL, cLb, cLi, sy2)
            else:
                sv("Raiva", char_stats.max_rage, 0, cL, cLb, cLi, sy2)
            # Espírito (coluna direita desta linha, antes vazia) — atributo
            # que alimenta HP5/MP5 fora de combate (10 pts = +1%, ver
            # ARQUITETURA_ONLINE.md Decisão 20.2). Sem "Itens" de verdade
            # ainda (nenhum equipamento concede Spirit hoje), mas já soma
            # PermanentStats (mesma mecânica roguelike dos outros 5
            # atributos) igual ao total usado em apply_char_stats_to_combat.
            from engine.components import PermanentStats as _PermSpirit
            _perm_inv = self.world.get_component(self.player_entity, _PermSpirit)
            _spirit_total = char_stats.spirit + (_perm_inv.spirit if _perm_inv else 0)
            sv("Espírito", _spirit_total, 0, cR, cRb, cRi, sy2)
        sy2 += ROW
