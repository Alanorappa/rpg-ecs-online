"""
hud_handlers.py — Mixin com o HUD principal (vida/mana/fúria, buffs,
minimapa, ouro, equipamentos rápidos, talentos alocados) e a barra
de cast/canalização exibida no centro inferior da tela durante
spells canalizadas. Separado de game.py para manter GameEngine
conciso. Esta classe NÃO deve ser instanciada diretamente — ela é
herdada por GameEngine, que fornece self.world, self.player_entity,
self.screen, self.font_sm e os demais atributos referenciados aqui.
"""
import pygame

from engine.components import (Channeling, CharacterStats, CombatStats, IceBlockEffect,
                        Inventory, PermanentStats, SpellCast, TileMovement, Wallet)
from client.colors import C_WHITE, C_YELLOW, C_GREEN, C_RED, C_GRAY, C_ORANGE
from ui.ui_sizes import UI


class HudHandlers:

    # ------------------------------------------------------------------

    def _draw_hud(self):
        combat_stats = self.world.get_component(self.player_entity, CombatStats)
        char_stats   = self.world.get_component(self.player_entity, CharacterStats)
        perm_stats   = self.world.get_component(self.player_entity, PermanentStats)
        if not combat_stats:
            return

        # --- Coordenadas (canto superior direito) — só com debug (F12) ---
        # O nome do mapa/zona foi pra barra da moldura do minimapa
        # (21/10/2026, ui/minimap.py::_draw_frame).
        import os
        tile_move = self.world.get_component(self.player_entity, TileMovement)
        if tile_move and self._hud_show_debug_stats:
            map_file = os.path.basename(self._current_map_file)
            coord_txt = f"Map: {map_file} | Tile: {tile_move.current_tile_x}, {tile_move.current_tile_y}"
            coord_surf = self.font_sm.render(coord_txt, False, (180, 180, 180))
            self.screen.blit(coord_surf, (self.screen.get_width() - coord_surf.get_width() - self._u(10), self._u(10)))

        # --- Retrato (atlas, 3x): busto + Vida / recurso / XP nas ranhuras
        #     (+ pinos de munição embaixo da faixa, só arqueiro) ---
        y = self._draw_portrait(combat_stats, char_stats)

        # --- Ataque CD ---
        if combat_stats.attack_cooldown_timer > 0:
            cd_surf = self.font_sm.render(
                f"Ataque CD: {combat_stats.attack_cooldown_timer:.1f}s", False, C_YELLOW)
        else:
            cd_surf = self.font_sm.render("Ataque: Pronto", False, C_GREEN)
        self.screen.blit(cd_surf, (self._u(10), y))
        y += self._u(18)

        # --- Ícones de efeitos de estado (debuffs/buffs ativos) ---
        from engine.components import StatusEffects as _SfxHUD
        from content.status_effects_data import EFFECT_DEFS as _EDEFS
        _sfx_hud = self.world.get_component(self.player_entity, _SfxHUD)
        if _sfx_hud and _sfx_hud.effects:
            _ICON = self._u(22)   # tamanho do ícone
            _GAP  = self._u(3)
            _ix   = self._u(10)
            for _etype, _eff in _sfx_hud.effects.items():
                _defn = _EDEFS.get(_etype)
                _col  = _defn.color if _defn else (180, 180, 180)
                _lbl  = (_defn.label[:4] if _defn else _etype[:4])
                # Fundo escuro + quadrado colorido
                pygame.draw.rect(self.screen, (20, 20, 20),
                                 (_ix - self._u(1), y - self._u(1), _ICON + self._u(2), _ICON + self._u(2)))
                pygame.draw.rect(self.screen, _col, (_ix, y, _ICON, _ICON))
                pygame.draw.rect(self.screen, (255, 255, 255),
                                 (_ix, y, _ICON, _ICON), 1)
                # Abreviação do efeito
                _lbl_surf = self.font_xs.render(_lbl, False, (255, 255, 255))
                self.screen.blit(_lbl_surf,
                                 (_ix + _ICON // 2 - _lbl_surf.get_width() // 2, y + self._u(1)))
                # Duração restante
                _dur_surf = self.font_xs.render(f"{_eff.duration:.0f}s", False, (230, 230, 230))
                self.screen.blit(_dur_surf,
                                 (_ix + _ICON // 2 - _dur_surf.get_width() // 2,
                                  y + _ICON - _dur_surf.get_height()))
                _ix += _ICON + _GAP
            y += _ICON + self._u(4)

        if not char_stats:
            return

        # Level e XP: removidos do HUD permanente por pedido do usuário — agora
        # só aparecem no painel de Skill Level (tecla L, skill_level_ui.py).
        # Reaparecerão aqui em formato ainda a definir.

        # --- Atributos brutos / ratings de combate — só com debug ativo (F12) ---
        # Redundante com a seção "Estatísticas" do painel de Inventário; por
        # padrão fica escondido pra não poluir o HUD permanente, ligar só
        # quando precisar verificar item/efeito/talento batendo nos stats.
        if self._hud_show_debug_stats:
            s = char_stats
            p = perm_stats
            attrs = (
                f"FOR:{s.strength}(+{p.strength if p else 0})  "
                f"INT:{s.intelligence}(+{p.intelligence if p else 0})  "
                f"AGI:{s.agility}(+{p.agility if p else 0})  "
                f"VIT:{s.vitality}(+{p.vitality if p else 0})  "
                f"DEF:{s.defense}(+{p.defense if p else 0})"
            )
            attr_surf = self.font_sm.render(attrs, False, C_GRAY)
            self.screen.blit(attr_surf, (self._u(10), y))
            y += attr_surf.get_height() + self._u(2)

            RPP = 20.0
            crit_pct  = combat_stats.crit_rating  * 100
            parry_pct = combat_stats.parry_rating / RPP
            dodge_pct = combat_stats.dodge_rating / RPP
            ratings = (
                f"Crit:{crit_pct:.1f}%  "
                f"Aparo:{parry_pct:.1f}%  "
                f"Esquiva:{dodge_pct:.1f}%"
            )
            rating_surf = self.font_sm.render(ratings, False, C_GRAY)
            self.screen.blit(rating_surf, (self._u(10), y))
            y += rating_surf.get_height() + self._u(2)

        # --- Ouro ---
        wallet = self.world.get_component(self.player_entity, Wallet)
        if wallet:
            # Pequeno círculo dourado + valor
            pygame.draw.circle(self.screen, (210, 175, 30), (self._u(18), y + self._u(7)), self._u(6))
            pygame.draw.circle(self.screen, (255, 220, 60), (self._u(18), y + self._u(7)), self._u(5))
            gold_surf = self.font_sm.render(f"{wallet.gold}", False, (255, 215, 0))
            self.screen.blit(gold_surf, (self._u(28), y))
            y += gold_surf.get_height() + self._u(2)

        # --- Inventário ---
        inv = self.world.get_component(self.player_entity, Inventory)
        if inv:
            inv_surf = self.font_sm.render(
                f"Mochila: {len(inv.items)}/{inv.max_slots}", False, C_GRAY)
            self.screen.blit(inv_surf, (self._u(10), y))
            y += inv_surf.get_height() + self._u(2)

        # --- Talentos pendentes ---
        from engine.components import TalentTree
        tt = self.world.get_component(self.player_entity, TalentTree)
        if tt and tt.available_points > 0:
            blink = int(pygame.time.get_ticks() / 500) % 2 == 0
            color = (180, 120, 255) if blink else (130, 80, 220)
            tal_surf = self.font_md.render(
                f"+{tt.available_points} TALENTO(S)! Pressione T para alocar", False, color)
            self.screen.blit(tal_surf, (self._u(10), y))

        # --- Barra de cast / canalização (centro inferior da tela) ---
        self._draw_cast_bar(char_stats)

    # ------------------------------------------------------------------
    # Retrato do personagem (atlas, 21/10/2026, pedido do usuário)
    # ------------------------------------------------------------------
    # Anel com o rosto do personagem (recorte da animação idle de frente —
    # mesmo mecanismo do jogo, engine/character_animation.py) + faixa com 3
    # ranhuras: Vida, recurso da classe, Experiência. Desenhado em 3x
    # (ui_atlas.PORTRAIT_SCALE, decisão do usuário: ranhuras legíveis).
    # Valores exatos no tooltip ao passar o mouse na ranhura.

    _PORTRAIT_FILL = {   # (cor da faixa de cima, cor da faixa de baixo)
        "hp":    ((235, 80, 70), (175, 30, 30)),
        "mana":  ((110, 150, 255), (50, 90, 220)),
        "rage":  ((240, 120, 60), (190, 70, 25)),
        "conc":  ((255, 190, 80), (215, 130, 20)),
        "xp":    ((190, 140, 245), (130, 80, 200)),
    }

    def _portrait_values(self, combat_stats, cs) -> list:
        """[(chave_de_cor, rótulo, atual, máximo)] das 3 ranhuras."""
        vals = [("hp", "Vida", combat_stats.current_hp, combat_stats.max_hp)]
        if cs is None:
            return vals
        if cs.class_id == "mago":
            vals.append(("mana", "Mana", cs.mana, cs.max_mana))
        elif cs.class_id == "arqueiro":
            vals.append(("conc", "Concentração", int(cs.concentration), cs.max_concentration))
        else:
            vals.append(("rage", "Raiva", cs.rage, cs.max_rage))
        vals.append(("xp", "Experiência", cs.current_xp, cs.xp_to_next_level))
        return vals

    def _portrait_face(self, cs) -> "pygame.Surface | None":
        """Busto do personagem já recortado na abertura do anel (camada do
        tamanho da peça do retrato). Sem animação: 1º frame do idle de
        frente (pedido do usuário). Cabeça centralizada na abertura pelo
        próprio desenho do frame (colunas da cabeça), topo da cabeça a
        PORTRAIT_BUST_TOP px do topo da abertura — mostra o que couber do
        busto."""
        if cs is None:
            return None
        key = ("bust", cs.class_id)
        layer = self._portrait_cache.get(key)
        if layer is not None:
            return layer
        from engine.character_animation import get_character_frame
        from ui import ui_atlas
        frame = get_character_frame(cs.class_id, "idle", "down", 0)
        if frame is None:
            return None
        fw, fh = frame.get_size()
        rows = [y for y in range(fh) if any(frame.get_at((x, y))[3] for x in range(fw))]
        if not rows:
            return None
        head_top = rows[0]
        head_cols = [x for x in range(fw)
                     if any(frame.get_at((x, y))[3] for y in range(head_top, min(fh, head_top + 12)))]
        head_cx = (head_cols[0] + head_cols[-1] + 1) / 2
        mask = ui_atlas.portrait_opening_mask()
        mb = mask.get_bounding_rect()
        layer = pygame.Surface(mask.get_size(), pygame.SRCALPHA)
        layer.blit(frame, (round(mb.centerx - head_cx), mb.top + ui_atlas.PORTRAIT_BUST_TOP - head_top))
        layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        self._portrait_cache[key] = layer
        return layer

    def _portrait_ammo(self, cs) -> "tuple | None":
        """(pinos, flechas, máximo) da aljava do arqueiro; None pra outras
        classes. Pinos = proporção da aljava × PORTRAIT_AMMO_MAX (não é a
        contagem exata — pedido do usuário)."""
        if cs is None or cs.class_id != "arqueiro":
            return None
        from engine.components import Equipment as _EqAmmo
        from ui import ui_atlas
        eq = self.world.get_component(self.player_entity, _EqAmmo)
        quiver = eq.slots.get("offhand") if eq else None
        if not quiver or getattr(quiver, "item_type", "") != "quiver":
            return (0, 0, 0)
        ratio = max(0.0, min(1.0, quiver.arrow_count / max(1, quiver.max_arrows)))
        pins = round(ratio * ui_atlas.PORTRAIT_AMMO_MAX)
        if pins == 0 and quiver.arrow_count > 0:
            pins = 1      # ainda tem flecha: nunca mostra a aljava vazia
        return (pins, quiver.arrow_count, quiver.max_arrows)

    def _draw_portrait(self, combat_stats, cs) -> int:
        """Desenha o retrato no canto superior esquerdo; devolve o y logo
        abaixo dele (onde o resto do HUD continua)."""
        from ui import ui_atlas
        K = ui_atlas.PORTRAIT_SCALE
        x0, y0 = self._u(10), self._u(10)
        piece = ui_atlas.piece_raw(ui_atlas.PORTRAIT)
        w, h = piece.get_size()
        grooves = ui_atlas.portrait_grooves()
        vals = self._portrait_values(combat_stats, cs)
        fills = tuple(round(g[2] * max(0.0, min(1.0, cur / max(1, mx))))
                      for g, (_k, _l, cur, mx) in zip(grooves, vals))
        face = self._portrait_face(cs)
        ammo = self._portrait_ammo(cs)
        key = ("portrait", fills, tuple(k for k, *_ in vals), id(face), ammo[0] if ammo else None)
        cached = self._portrait_cache.get("portrait")
        if cached is None or cached[0] != key:
            raw = piece.copy()                      # já traz o fundo do círculo
            if face is not None:
                raw.blit(face, (0, 0))              # busto só dentro da abertura do anel
            for (gx, gy, gw, gh), (ck, *_), fw in zip(grooves, vals, fills):
                if fw > 0:
                    top, base = self._PORTRAIT_FILL[ck]
                    raw.fill(top, (gx, gy, fw, 1))
                    raw.fill(base, (gx, gy + 1, fw, gh - 1))
            if ammo and ammo[0] > 0:
                pin = ui_atlas.piece_raw(ui_atlas.PORTRAIT_AMMO_PIN)
                px0, py0 = ui_atlas.PORTRAIT_AMMO_POS
                for i in range(ammo[0]):
                    raw.blit(pin, (px0 + i * ui_atlas.PORTRAIT_AMMO_STEP, py0))
            cached = (key, pygame.transform.scale(raw, (w * K, h * K)))
            self._portrait_cache["portrait"] = cached
        self.screen.blit(cached[1], (x0, y0))

        # Nome e nível acima da faixa (à direita do anel)
        if cs is not None:
            name_s = self.font_sm.render(f"{cs.name}   Nv {cs.level}", False, (240, 225, 185))
            self.screen.blit(name_s, name_s.get_rect(bottomleft=(x0 + 30 * K, y0 + 7 * K)))

        # Tooltip com os valores exatos ao passar o mouse numa ranhura
        mx_, my_ = pygame.mouse.get_pos()
        for (gx, gy, gw, gh), (ck, label, cur, mx) in zip(grooves, vals):
            hit = pygame.Rect(x0 + gx * K, y0 + (gy - 1) * K, gw * K, (gh + 2) * K)
            if hit.collidepoint(mx_, my_):
                lines = []
                if ck == "conc":
                    _tm = self.world.get_component(self.player_entity, TileMovement)
                    _rate = getattr(combat_stats, "concentration_regen_moving" if _tm and _tm.is_moving
                                    else "concentration_regen_idle", 0.0)
                    if _rate > 0:
                        lines.append((f"+{_rate:.0f}/s", (200, 200, 200)))
                self._pending_tooltip = (mx_, my_, f"{label}: {cur}/{mx}", lines)
        if ammo is not None:
            px0, py0 = ui_atlas.PORTRAIT_AMMO_POS
            pw = (ui_atlas.PORTRAIT_AMMO_MAX - 1) * ui_atlas.PORTRAIT_AMMO_STEP + ui_atlas.PORTRAIT_AMMO_PIN[2]
            hit = pygame.Rect(x0 + px0 * K, y0 + py0 * K, pw * K, ui_atlas.PORTRAIT_AMMO_PIN[3] * K)
            if hit.collidepoint(mx_, my_):
                title = f"Aljava: {ammo[1]}/{ammo[2]}" if ammo[2] else "Sem aljava"
                self._pending_tooltip = (mx_, my_, title, [])
        return y0 + h * K + self._u(6)

    # ------------------------------------------------------------------
    # Moldura do minimapa: nome do mapa/zona + fileira de ícones (21/10/2026)
    # ------------------------------------------------------------------
    # Ícones (ordem do atlas): bolsa = inventário, espadas = PvP (F1),
    # engrenagem = menu, pessoa = amigos (ainda não existe), troféu =
    # ranking. Aceso = botão apertado OU janela correspondente aberta
    # (decisão do usuário). Clique age no SOLTAR, como todo botão do atlas;
    # o APERTAR é consumido aqui pra não vazar pro mundo.

    def _minimap_title(self) -> str:
        import os
        if self._current_zone:
            return self._current_zone
        return os.path.splitext(os.path.basename(self._current_map_file))[0].replace("_", " ").title()

    def _minimap_icons_lit(self) -> list:
        is_open = (self._show_inventory, self._arena_modal_open, self._show_pause,
                   False, self._show_ranking)
        return [on or self._mm_icon_pressed == i for i, on in enumerate(is_open)]

    def _minimap_icon_at(self, pos) -> int:
        if self._map_overlay.is_open:
            return -1
        for i, rect in enumerate(self._minimap.icon_rects()):
            if rect.collidepoint(pos):
                return i
        return -1

    def _handle_minimap_icon_event(self, event) -> bool:
        """True = clique consumido (apertar/soltar num ícone da moldura)."""
        if getattr(event, "button", 0) != 1:
            return False
        idx = self._minimap_icon_at(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN:
            if idx < 0:
                return False
            self._mm_icon_pressed = idx
            return True
        if event.type == pygame.MOUSEBUTTONUP and self._mm_icon_pressed >= 0:
            pressed, self._mm_icon_pressed = self._mm_icon_pressed, -1
            if idx == pressed:
                self._minimap_icon_action(idx)
            return True
        return False

    def _toggle_inventory(self) -> None:
        """Abre/fecha o inventário — tecla I e ícone da bolsa (ponto único)."""
        from ui.sound_manager import SOUNDS as _SND_MM
        already = self._show_inventory
        self._close_all_modals()
        if not already:
            self._show_inventory = True
            _SND_MM.play_ui("inventory_open")

    def _toggle_ranking(self) -> None:
        """Abre/fecha o Ranking — tecla K e ícone do troféu (ponto único)."""
        already = self._show_ranking
        self._close_all_modals()
        if not already:
            self._show_ranking = True
            self._ranking_system.on_open()

    def _minimap_icon_action(self, idx: int) -> None:
        if idx == 0:
            self._toggle_inventory()
        elif idx == 1:
            self._open_bg_queue_modal()          # mesma ação do F1 (toggle)
        elif idx == 2:
            self._show_pause = not self._show_pause
        elif idx == 3:
            from ui.combat_log import LOG as _LOG_MM
            _LOG_MM.add("Amigos: em breve.", (180, 180, 180))
        elif idx == 4:
            self._toggle_ranking()

    def _minimap_icon_tooltip(self) -> None:
        mx, my = pygame.mouse.get_pos()
        idx = self._minimap_icon_at((mx, my))
        if idx >= 0:
            self._pending_tooltip = (mx, my, self._minimap.ICON_TIPS[idx], [])

    def _draw_bg_kda_hud(self) -> None:
        """HUD permanente de topo-centro durante o battleground de teste
        (02/08/2026, pedido do usuário) — "Kills: N | Deaths: N | Farm: N |
        Gold: N", Gold = acumulado da partida (sem contar o inicial).
        Gated em InstanceInventoryUIState.active (mesma flag "estou na BG"
        já usada pra hotbar/consumable bar/death UI); valores vêm prontos
        via STATS_UPDATE (match_kills/deaths/farm/gold, ver
        client/network_handlers.py::_handle_msg_stats_update)."""
        from ui.ui_components import InstanceInventoryUIState as _IIUSHud
        iius = self.world.get_component(self.player_entity, _IIUSHud)
        if iius is None or not iius.active:
            return
        txt = (f"Kills: {iius.match_kills} | Deaths: {iius.match_deaths} | "
               f"Assists: {iius.match_assists} | "
               f"Farm: {iius.match_farm} | Gold: {iius.match_gold}")
        surf = self.font_sm.render(txt, False, (230, 220, 200))
        sw, _ = self.screen.get_size()
        x = (sw - surf.get_width()) // 2
        y = self._u(10)
        pad_x, pad_y = self._u(10), self._u(4)
        bg_rect = pygame.Rect(x - pad_x, y - pad_y,
                              surf.get_width() + pad_x * 2, surf.get_height() + pad_y * 2)
        bg = pygame.Surface(bg_rect.size, pygame.SRCALPHA)
        bg.fill((0, 0, 0, 140))
        self.screen.blit(bg, bg_rect.topleft)
        self.screen.blit(surf, (x, y))

    def _draw_cast_bar(self, char_stats: "CharacterStats | None") -> None:
        """Barra de cast/canalização — exibida no centro inferior da tela."""
        sw, sh = self.screen.get_size()
        BAR_W, BAR_H = self._u(280), self._u(20)
        bx = sw // 2 - BAR_W // 2
        by = sh - self._u(140)  # acima da hotbar

        spell_cast = self.world.get_component(self.player_entity, SpellCast)
        channeling = self.world.get_component(self.player_entity, Channeling)
        ice_block  = self.world.get_component(self.player_entity, IceBlockEffect)

        if spell_cast:
            ratio  = min(1.0, spell_cast.elapsed / max(0.01, spell_cast.cast_time))
            label  = f"Lançando... {spell_cast.elapsed:.1f}/{spell_cast.cast_time:.1f}s"
            bar_col = (255, 160, 60)
            bg_col  = (60, 30, 0)
        elif channeling:
            remaining = max(0.0, channeling.duration - channeling.elapsed)
            ratio  = remaining / max(0.01, channeling.duration)
            label  = f"Canalizando... {remaining:.1f}s"
            bar_col = (255, 100, 20)
            bg_col  = (50, 20, 0)
        elif getattr(self, "_portal_cast_active", None):
            # Cast de portal (30/09/2026, client/portal_handlers.py) —
            # mesmo padrão visual de SpellCast/Channeling, sem componente
            # ECS próprio (estado puramente client-side, confirmado pelo
            # servidor via PORTAL_CAST_START/CANCEL).
            _pc = self._portal_cast_active
            remaining = max(0.0, _pc["duration"] - _pc["elapsed"])
            ratio  = remaining / max(0.01, _pc["duration"])
            label  = f"Teleportando... {remaining:.1f}s"
            bar_col = (120, 200, 255)
            bg_col  = (10, 30, 50)
        else:
            return

        # Fundo
        bg_r = pygame.Rect(bx - self._u(2), by - self._u(2), BAR_W + self._u(4), BAR_H + self._u(4))
        pygame.draw.rect(self.screen, (0, 0, 0), bg_r, border_radius=4)
        pygame.draw.rect(self.screen, bg_col, (bx, by, BAR_W, BAR_H), border_radius=3)
        pygame.draw.rect(self.screen, bar_col, (bx, by, int(BAR_W * ratio), BAR_H), border_radius=3)
        pygame.draw.rect(self.screen, (200, 200, 200), (bx, by, BAR_W, BAR_H), 1, border_radius=3)
        txt = self.font_sm.render(label, False, (255, 255, 255))
        self.screen.blit(txt, (bx + BAR_W // 2 - txt.get_width() // 2,
                               by + BAR_H // 2 - txt.get_height() // 2))
