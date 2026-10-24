"""
inventory_handlers.py — Mixin com o painel de equipamentos/inventário
(tecla I): equipar/desequipar itens, clique em slots, uso de consumíveis
e desenho do painel (atlas de UI, ui/atlas_window.py). Separado de game.py para manter GameEngine conciso.
Esta classe NÃO deve ser instanciada diretamente — ela é herdada por
GameEngine, que fornece self.world, self._my_eid, self._net e os demais
atributos referenciados aqui (inclusive `_inv_press`, PressState dos
botões do painel, e `_mm_safe_area`, área segura das janelas do atlas).
"""
import math

import pygame

from engine.components import (CharacterStats, CombatStats, Equipment, Inventory,
                          PlayerLightSource, Position, Wallet)
from ui.combat_log import LOG
from ui.icon_manager import ICONS
from ui.sound_manager import SOUNDS
from engine.stat_fns import add_modifier, learn_recipe, remove_modifier
from ui import atlas_window as aw
from ui import ui_atlas
from ui.ui_helpers import item_tooltip_lines


class InventoryHandlers:

    _RARITY_COLORS = {
        "common":    (200, 200, 200),
        "uncommon":  ( 30, 200,  30),
        "rare":      ( 80, 140, 255),
        "epic":      (180,  50, 255),
        "legendary": (224, 135,  47),
        "mythic":    (221,  68,  68),
    }

    # ------------------------------------------------------------------ #
    #  Item concede quest (25/07/2026, pedido do usuário; REVISADO no
    #  mesmo dia — usuário rejeitou o popup custom, pediu reaproveitar o
    #  MESMO modal de quest do NPC) — clique direito num item registrado
    #  em content/quests_data.py::ITEM_GRANTS_QUEST abre o
    #  QuestDialogSystem (ui/quest_system.py) direto no estado "detail",
    #  igual à quest de um NPC, só que sem NPC real por trás
    #  (QuestDialogSystem.open_for_item). Fecha o inventário (o jogador
    #  clicou direito com a bag aberta). Recusar/fechar o modal NÃO
    #  descarta o item: continua na bag, próximo clique direito reabre o
    #  mesmo diálogo, até aceitar (ou o jogador deletar o item
    #  manualmente). Aceitar manda o MESMO QUEST_ACCEPT que o diálogo de
    #  NPC já usa. Uma vez a quest ativa/completa, o clique direito volta
    #  ao fluxo normal (equipar/consumir/oferecer em troca).
    # ------------------------------------------------------------------ #

    def _try_open_item_quest_dialog(self, item) -> bool:
        """True = item concede quest ainda não aceita/completada — abriu
        o modal de quest (QuestDialogSystem) e fechou o inventário,
        clique direito NÃO deve equipar/consumir desta vez. False = item
        normal (ou quest já resolvida/qid inexistente) — segue o fluxo de
        sempre."""
        from content.quests_data import ITEM_GRANTS_QUEST
        qid = ITEM_GRANTS_QUEST.get(item.name)
        if not qid:
            return False
        if not self._quest_dialog.open_for_item(qid, item.name):
            return False   # já aceita/completa — item vira item normal
        self._show_inventory   = False
        self._selected_inv_idx = -1
        SOUNDS.play_ui("button_click")
        return True

    # ------------------------------------------------------------------ #
    #  Equip / Unequip
    # ------------------------------------------------------------------ #

    def _equip_item(self, item):
        inv          = self.world.get_component(self.player_entity, Inventory)
        equip        = self.world.get_component(self.player_entity, Equipment)
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        if not (inv and equip and combat_stats):
            return

        # Posição ANTES de qualquer mutação local — débito A4, é o que vai
        # pro servidor no lugar do item em si (ver _send_equip_item).
        try:
            _inv_index = inv.items.index(item)
        except ValueError:
            return

        target_slot = item.slot
        if target_slot not in equip.slots:
            return

        char = self.world.get_component(self.player_entity, CharacterStats)

        # Restrição de armor_class por classe do personagem — feedback
        # imediato; servidor valida de novo em equip_item_from_inventory
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
        if item.item_type == "quiver":
            from debug.archer_debug import ADBG_CLIENT as _ADBG_c_equip
            _ADBG_c_equip.log("EQUIP", self.player_entity, -1,
                              quiver_item_id=item.item_id, inv_index=_inv_index,
                              arrow_count=getattr(item, "arrow_count", -1),
                              quiver_obj_id=id(item))
        self._send_equip_item(_inv_index)

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
        self._send_unequip_item(slot_name)

    def _use_consumable(self, item, idx: int, inv) -> None:
        """Usa um item consumível do inventário."""
        from engine.components import CombatStats, CombatState, ActiveRegen, ConsumableBar as _CB
        from ui.combat_log import LOG
        from ui.floating_text import FLT

        # Modo online: delega para ConsumableSystem (que notifica o servidor)
        if getattr(self, "_net", None) and self._consumable_system:
            cbar = self.world.get_component(self.player_entity, _CB)
            self._consumable_system._use_consumable(
                self.player_entity, item.item_id, cbar
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

    def _toggle_item_light(self, item) -> None:
        """Acende/apaga item de bag sob demanda (23/09/2026, ver
        engine/components.py::PlayerLightSource). NUNCA muda estado local
        na hora — servidor é quem decide de verdade (confirma posse real +
        Item.light_source) e a confirmação chega via PLAYER_LIGHT_UPDATE
        (client/network_handlers.py::_handle_msg_player_light_update),
        mesmo espírito de `_use_consumable` (predição LOCAL só quando
        offline; online sempre espera o servidor)."""
        if not getattr(self, "_net", None):
            return   # sem modo offline pra luz de item — feature 100% online
        from shared.messages import MsgType as _MTLight
        self._net.send(_MTLight.TOGGLE_ITEM_LIGHT, {"item_id": item.item_id})

    # ------------------------------------------------------------------ #
    #  Painel (atlas de UI, janela 2 do mockup — 21/10/2026)
    # ------------------------------------------------------------------ #
    # Janela escura com barra de título e X; à esquerda os 10 slots de
    # equipamento em volta do boneco animado (decisão do usuário: Cabeça/
    # Ombros/Torso/Punhos à esquerda, Pescoço/Anel/Luvas/Botas à direita,
    # Mão Princ./Mão Secund. embaixo), divisória, grade da mochila (slots
    # escuros) à direita, estatísticas embaixo da divisória horizontal.
    # Nome do slot só no tooltip (decisão do usuário). Cliques agem no
    # SOLTAR (esquerdo E direito) — ui/atlas_window.py.

    # Medidas em px RAW (slot de ícone 32x32 — aw.SLOT). Equipamento:
    # coluna esq. | boneco (armas embaixo dele) | coluna dir.
    _INV_PAD     = 6                          # folga da borda da janela
    _INV_TOP     = 36                         # topo dos slots (abaixo do cabeçalho)
    _INV_GAP     = 2                          # entre slots de equipamento
    _INV_EQ_POS = {   # slot → (coluna, linha) — mockup
        "head": ("l", 0), "shoulders": ("l", 1), "chest": ("l", 2), "wrists": ("l", 3),
        "neck": ("r", 0), "ring": ("r", 1), "gloves": ("r", 2), "boots": ("r", 3),
        "mainhand": ("w0", 3), "offhand": ("w1", 3),
    }
    _INV_BAG_COLS = 5

    @classmethod
    def _inv_eq_x(cls) -> dict:
        s, p = aw.SLOT, cls._INV_PAD
        lx = p
        w0 = lx + s + 6
        w1 = w0 + s + cls._INV_GAP
        rx = w1 + s + 6
        return {"l": lx, "w0": w0, "w1": w1, "r": rx}

    def _inv_stat_rows(self, combat_stats, char_stats) -> list:
        """[(rótulo, total, bônus_de_item, fmt)] em 2 colunas (esq., dir.) por
        linha. "Base" = total - bônus; "Itens" = só equipamento
        (CombatStats.equipment_bonus, Modifier.source=="equipment")."""
        eq = combat_stats.equipment_bonus
        pct = lambda v: f"{v:.1f}%"
        num = lambda v: f"{int(round(v))}"
        rows = [
            (("HP", combat_stats.max_hp, eq("stamina") * 10, num),
             ("Acerto", combat_stats.acerto, eq("acerto"), pct)),
            (("Atq. Físico", combat_stats.attack_power, eq("attack_power"), num),
             ("Esquiva", combat_stats.dodge_rating / 20, eq("dodge_rating") / 20, pct)),
            (("Atq. Mágico", combat_stats.spell_power, eq("spell_power"), num),
             ("Aparo", combat_stats.parry_rating / 20, eq("parry_rating") / 20, pct)),
            # Vel. Ataque: bônus de item pode ser negativo (arma mais rápida).
            (("Armadura", combat_stats.armor, eq("armor"), num),
             ("Vel. Ataque", combat_stats.attack_interval, eq("attack_interval"), lambda v: f"{v:.2f}s")),
            (("Estamina", combat_stats.stamina, eq("stamina") * 10, num),
             ("Crítico", combat_stats.crit_rating * 100, eq("crit_rating") * 100, pct)),
        ]
        if char_stats:
            # Recurso da classe (class_id direto, mesmo critério do HUD) —
            # equipamento nunca modifica mana/concentração/raiva hoje.
            from engine.stats_system import class_resource
            _, _res_label, _, _res_max = class_resource(char_stats)
            res = (_res_label, _res_max, 0, num)
            # Espírito (HP5/MP5 fora de combate) soma PermanentStats igual
            # ao total usado em apply_char_stats_to_combat.
            from engine.components import PermanentStats as _PermSpirit
            _perm = self.world.get_component(self.player_entity, _PermSpirit)
            spirit = char_stats.spirit + (_perm.spirit if _perm else 0)
            rows.append((res, ("Espírito", spirit, 0, num)))
        return rows

    def _inv_layout(self, stat_rows) -> dict:
        """Medidas da janela: equipamento + mochila (slots de aw.SLOT) em
        cima, estatísticas embaixo — cresce com o texto (fonte acompanha a
        escala de UI). Tudo em px RAW, janela arredondada pra tiles."""
        T, S = aw.T, aw.S
        fsm = self.font_sm
        lab_w = max(fsm.size(r[i][0] + ":")[0] for r in stat_rows for i in (0, 1)) / S
        val_w = max(max(fsm.size(r[i][3](r[i][1] - r[i][2]))[0],
                        fsm.size("Base")[0]) for r in stat_rows for i in (0, 1)) / S
        itm_w = max(max(fsm.size("+" + r[i][3](r[i][2]))[0], fsm.size("Itens")[0])
                    for r in stat_rows for i in (0, 1)) / S
        col_w = lab_w + 6 + val_w + 8 + itm_w
        eq_x = self._inv_eq_x()
        eq_right = eq_x["r"] + aw.SLOT
        bag_w = aw.grid_px(self._INV_BAG_COLS)
        eq_bottom = self._INV_TOP + 4 * aw.SLOT + 3 * self._INV_GAP
        lh = math.ceil(fsm.get_linesize() / S)
        n_stats = 2 * len(stat_rows)
        # Estatísticas numa 3ª coluna à direita da mochila (a janela não
        # cabe na altura da área segura com elas embaixo); se a largura não
        # couber (fonte grande), voltam pra baixo em 2 colunas.
        area = self._mm_safe_area()
        bag_x0 = eq_right + 16
        stats_x0 = bag_x0 + bag_w + 16
        side_w = stats_x0 + col_w + self._INV_PAD + 2
        if side_w * S <= area.w:
            tw = math.ceil(side_w / T)
            stats_h = (n_stats + 1) * lh
            th = math.ceil((max(eq_bottom, self._INV_TOP + stats_h) + 8) / T)
            return {"tw": tw, "th": th, "lab_w": lab_w, "val_w": val_w, "col_w": col_w, "lh": lh,
                    "eq_x": eq_x, "bag_x0": bag_x0, "side": True, "stats_x0": stats_x0,
                    "div_xs": ((eq_right + bag_x0) // 2, (bag_x0 + bag_w + stats_x0) // 2),
                    "div_y": th * T - 6}
        top_w = eq_right + 16 + bag_w + self._INV_PAD
        tw = max(math.ceil((top_w + 2) / T), math.ceil((2 * col_w + 16 + 2 * T) / T))
        div_y = eq_bottom + 8
        stats_h = (len(stat_rows) + 1) * lh + 8
        th = math.ceil((div_y + 6 + stats_h + 4) / T)
        bag_x0 = tw * T - 2 - self._INV_PAD - bag_w
        return {"tw": tw, "th": th, "lab_w": lab_w, "val_w": val_w, "col_w": col_w, "lh": lh,
                "eq_x": eq_x, "bag_x0": bag_x0, "side": False, "div_y": div_y,
                "div_xs": ((eq_right + bag_x0) // 2,)}

    def _draw_inventory_panel(self):
        inv          = self.world.get_component(self.player_entity, Inventory)
        equip        = self.world.get_component(self.player_entity, Equipment)
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        if not (inv and equip and combat_stats):
            return
        T, S = aw.T, aw.S
        char = self.world.get_component(self.player_entity, CharacterStats)
        viewer_cls = char.class_id if char else None
        events = self._ui_events
        stat_rows = self._inv_stat_rows(combat_stats, char)
        lay = self._inv_layout(stat_rows)
        tw, th = lay["tw"], lay["th"]
        eq_x, bag_x0, top = lay["eq_x"], lay["bag_x0"], self._INV_TOP
        f = aw.Frame(self.screen, events, tw, th, area=self._mm_safe_area(), press=self._inv_press,
                     font=self.font_md, raw=ui_atlas.titled_dark_window_raw(tw, th), overlay=False)
        mx, my = pygame.mouse.get_pos()
        right_up = any(e.type == pygame.MOUSEBUTTONUP and e.button == 3 for e in events)
        left_down = f.clicked
        ip = aw.slot_icon_px()

        f.text("Inventário", (tw - 1) * T / 2, aw.TITLE_BAR_Y, (240, 228, 200))
        if f.close_button("inv:close", tw - 1):
            self._show_inventory = False
            self._selected_inv_idx = -1

        # ---- Cabeçalho: "Nome, Classe Nível N" sobre o boneco ----
        mid_x = (eq_x["w0"] + eq_x["w1"] + aw.SLOT) / 2
        if char:
            f.text(f"{char.name}, {char.class_id.capitalize()} Nível {char.level}",
                   mid_x, 25, (240, 225, 185), self.font_sm)

        # ---- Equipamento (em volta do boneco) ----
        step = aw.SLOT + self._INV_GAP
        for slot_name, (c, r) in self._INV_EQ_POS.items():
            item = equip.slots.get(slot_name)
            locked = slot_name == "offhand" and equip.is_offhand_locked()
            icon = aw.item_icon(item, self.font_sm)
            hov, fired = f.slot(f"inv:eq:{slot_name}", eq_x[c], top + r * step, icon, dim=locked)
            if hov:
                label = Equipment.SLOT_LABELS[slot_name]
                if item:
                    lines = item_tooltip_lines(item, viewer_cls)
                    lines.append(("Clique p/ desequipar", (140, 140, 140)))
                    self._pending_tooltip = (mx, my, item.name, lines,
                                             self._RARITY_COLORS.get(item.rarity, (255, 220, 100)))
                else:
                    sub = [("Bloqueado: arma de duas mãos", (200, 110, 110))] if locked else []
                    self._pending_tooltip = (mx, my, label, sub, (200, 190, 160))
                if item and (fired or right_up):
                    self._unequip_slot(slot_name)
        frame = self._inv_preview_frame(char)
        if frame is not None:   # boneco no centro do espaço acima das armas
            cy = top + (3 * step - self._INV_GAP) / 2
            f.overlays.append((frame, mid_x - frame.get_width() / S / 2,
                               cy - frame.get_height() / S / 2))

        for dx in lay["div_xs"]:
            f.divider_v(dx, T, lay["div_y"])
        if not lay["side"]:
            f.divider_h(lay["div_y"], T, (tw - 1) * T)

        # ---- Mochila ----
        bag_w = aw.grid_px(self._INV_BAG_COLS)
        f.text(f"Mochila {len(inv.items)}/{inv.max_slots}", bag_x0, 25,
               (190, 170, 120), self.font_sm, anchor="midleft")
        wallet = self.world.get_component(self.player_entity, Wallet)
        if wallet:
            f.text(f"{wallet.gold} ouro", bag_x0 + bag_w, 25,
                   (255, 215, 0), self.font_sm, anchor="midright")
        pls = self.world.get_component(self.player_entity, PlayerLightSource)
        cells = []
        for item in inv.items:
            if item is None:
                cells.append(None)
                continue
            lit = (getattr(item, "light_source", None) and pls is not None
                   and pls.active and pls.item_id == item.item_id)
            ic = (ICONS.get_active(ICONS.item_key(item), ip) if lit
                  else aw.item_icon(item, self.font_sm))
            cells.append((ic, item.stack if getattr(item, "max_stack", 1) > 1 else 0))
        rows = math.ceil(inv.max_slots / self._INV_BAG_COLS)
        hov_i, hit_i = f.grid("inv:bag", bag_x0, top, self._INV_BAG_COLS, rows,
                              cells, selected=self._selected_inv_idx, count_font=self.font_xs)
        if hov_i >= inv.max_slots:
            hov_i = -1
        item = inv.items[hov_i] if 0 <= hov_i < len(inv.items) else None
        if item is not None:
            del_hint = "DEL p/ deletar | " if hov_i == self._selected_inv_idx else ""
            lines = item_tooltip_lines(item, viewer_cls)
            name_col = self._RARITY_COLORS.get(item.rarity, (255, 220, 100))
            if getattr(item, "consumable", None):
                lines.append((f"{del_hint}Arraste p/ barra de consumíveis | Dir. p/ usar", (140, 140, 140)))
                self._pending_tooltip = (mx, my, item.name, lines, name_col)
                if left_down:   # arrastar pra barra de consumíveis começa no APERTAR
                    drag = self._get_drag()
                    drag.kind, drag.source, drag.payload, drag.active = \
                        "consumable", "inventory", item.item_id, True
            else:
                lines.append((f"{del_hint}Clique dir. p/ equipar | Shift p/ comparar", (140, 140, 140)))
                self._pending_tooltip = (mx, my, item.name, lines, name_col,
                                         item, equip.slots.get(item.slot))
        if hit_i >= 0:
            self._selected_inv_idx = hit_i if (self._selected_inv_idx != hit_i and
                                               hit_i < len(inv.items) and inv.items[hit_i]) else -1
        elif f.released and f.hover(0, 0, tw * T, th * T):
            self._selected_inv_idx = -1   # clique fora da mochila deseleciona
        if right_up and item is not None:
            self._inventory_right_click(item, hov_i, inv)

        # ---- Estatísticas (rótulo | Base | Itens): 1 coluna à direita da
        #      mochila, ou 2 colunas embaixo (ver _inv_layout) ----
        lh = lay["lh"]
        hdr, val_col, item_col, none_col = (160, 140, 100), (255, 220, 120), (120, 200, 120), (90, 80, 60)
        if lay["side"]:
            y = top - lh
            cols_x = (lay["stats_x0"],)
            stat_lines = [(e,) for i in (0, 1) for e in (r[i] for r in stat_rows)]
        else:
            y = lay["div_y"] + 6
            cols_x = (T + 4, T + 4 + lay["col_w"] + 16)
            stat_lines = stat_rows
        for cx in cols_x:
            f.text("Base", cx + lay["lab_w"] + 6, y, hdr, self.font_xs, anchor="topleft")
            f.text("Itens", cx + lay["lab_w"] + 6 + lay["val_w"] + 8, y, hdr, self.font_xs, anchor="topleft")
        y += lh
        for row in stat_lines:
            for (label, total, bonus, fmt), cx in zip(row, cols_x):
                f.text(label + ":", cx, y, hdr, self.font_sm, anchor="topleft")
                f.text(fmt(total - bonus), cx + lay["lab_w"] + 6, y, val_col, self.font_sm, anchor="topleft")
                ix = cx + lay["lab_w"] + 6 + lay["val_w"] + 8
                if abs(bonus) >= 0.05:
                    f.text(("+" if bonus > 0 else "") + fmt(bonus), ix, y, item_col, self.font_sm, anchor="topleft")
                else:
                    f.text("—", ix, y, none_col, self.font_sm, anchor="topleft")
            y += lh
        f.end()
        self._inv_panel_rect = pygame.Rect(f.px, f.py, f.pw, f.ph)

    def _inv_preview_frame(self, char):
        """Boneco animado (idle, de frente) — mesmo mecanismo do jogo
        (engine/character_animation.py), frame ampliado 2x no espaço RAW
        (acompanha o slot de ícone 32x32) e depois por SCALE."""
        if char is None:
            return None
        from engine.character_animation import get_character_frame
        frame = get_character_frame(char.class_id, "idle", "down", pygame.time.get_ticks())
        if frame is None:
            return None
        size = frame.get_width() * 2 * aw.S
        return pygame.transform.scale(frame, (size, size))

    def _inventory_right_click(self, item, idx: int, inv) -> None:
        """Clique direito (no soltar) num item da mochila: oferecer na troca
        aberta > item que concede quest > consumir > acender luz > equipar."""
        trade_ui = self._get_trade_ui()
        if trade_ui is not None and trade_ui.is_open:
            self._offer_trade_item(idx)
        elif self._try_open_item_quest_dialog(item):
            pass   # modal de quest consumiu o clique
        elif getattr(item, "consumable", None):
            self._use_consumable(item, idx, inv)
        elif getattr(item, "light_source", None):
            self._toggle_item_light(item)
        else:
            self._equip_item(item)
        self._selected_inv_idx = -1

    def _handle_inventory_click(self, event) -> None:
        """Clique com o inventário aberto: as ações agem no SOLTAR, dentro
        de _draw_inventory_panel (Frame do atlas). Aqui só se "segura" o
        clique (game.py roteia o MOUSEBUTTONDOWN pra cá enquanto o painel
        está aberto, pra ele não vazar pro mundo)."""
        return
