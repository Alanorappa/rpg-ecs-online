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

        # --- Zona atual + coordenadas (canto superior direito) ---
        import os
        map_name = os.path.splitext(os.path.basename(self._current_map_file))[0].replace("_", " ").title()
        zone_surf = self.font_sm.render(map_name, False, C_YELLOW)
        self.screen.blit(zone_surf, (self.screen.get_width() - zone_surf.get_width() - self._u(10), self._u(10)))

        tile_move = self.world.get_component(self.player_entity, TileMovement)
        if tile_move:
            map_file = os.path.basename(self._current_map_file)
            coord_txt = f"Map: {map_file} | Tile: {tile_move.current_tile_x}, {tile_move.current_tile_y}"
            coord_surf = self.font_sm.render(coord_txt, False, (180, 180, 180))
            self.screen.blit(coord_surf, (self.screen.get_width() - coord_surf.get_width() - self._u(10), self._u(28)))
            if self._current_zone:
                zone_txt  = self.font_sm.render(self._current_zone, False, (160, 200, 160))
                self.screen.blit(zone_txt, (self.screen.get_width() - zone_txt.get_width() - self._u(10), self._u(46)))

        y = self._u(10)  # cursor vertical

        # --- Nome do personagem ---
        if char_stats:
            name_surf = self.font_sm.render(
                f"{char_stats.name}  [{char_stats.class_id.capitalize()}]", False, (210, 185, 255))
            self.screen.blit(name_surf, (self._u(10), y))
            y += name_surf.get_height() + self._u(2)

        # --- HP ---
        hp_ratio = max(0, combat_stats.current_hp / max(1, combat_stats.max_hp))
        bar_w = self._u(UI.HUD_BAR_W)
        bar_h = self._u(UI.HUD_BAR_H)
        pygame.draw.rect(self.screen, (80, 0, 0),   (self._u(10), y, bar_w, bar_h))
        pygame.draw.rect(self.screen, C_RED,         (self._u(10), y, int(bar_w * hp_ratio), bar_h))
        hp_surf = self.font_sm.render(
            f"HP {combat_stats.current_hp}/{combat_stats.max_hp}", False, C_WHITE)
        self.screen.blit(hp_surf, (self._u(14), y))
        y += self._u(18)

        # --- Rage (Guerreiro) / Mana (Mago) / Aljava (Arqueiro) ---
        if char_stats:
            if char_stats.class_id == "mago":
                mana_ratio = char_stats.mana / max(1, char_stats.max_mana)
                pygame.draw.rect(self.screen, (0, 20, 80),    (self._u(10), y, bar_w, bar_h))
                pygame.draw.rect(self.screen, (50, 100, 255), (self._u(10), y, int(bar_w * mana_ratio), bar_h))
                mana_surf = self.font_sm.render(
                    f"Mana {char_stats.mana}/{char_stats.max_mana}", False, C_WHITE)
                self.screen.blit(mana_surf, (self._u(14), y))
            elif char_stats.class_id == "arqueiro":
                # Barra de Concentração — laranja forte (C_ORANGE); vira
                # vermelha (C_RED) perto de esvaziar, mesmo aviso de sempre.
                # Pedido do usuário 15/07/2026: era azul, confundia com mana.
                _conc_ratio = char_stats.concentration / max(1, char_stats.max_concentration)
                _conc_col   = C_ORANGE if _conc_ratio > 0.3 else C_RED
                pygame.draw.rect(self.screen, (55, 30, 5),   (self._u(10), y, bar_w, bar_h))
                pygame.draw.rect(self.screen, _conc_col, (self._u(10), y, int(bar_w * _conc_ratio), bar_h))
                _cs_conc  = self.world.get_component(self.player_entity, CombatStats)
                _tm_conc  = self.world.get_component(self.player_entity, TileMovement)
                _moving   = _tm_conc.is_moving if _tm_conc else False
                _rate     = (getattr(_cs_conc, "concentration_regen_moving", 0.0)
                             if _moving else
                             getattr(_cs_conc, "concentration_regen_idle",   0.0))
                _rate_str = f"  (+{_rate:.0f}/s)" if _rate > 0 else ""
                conc_surf = self.font_sm.render(
                    f"Conc. {int(char_stats.concentration)}/{char_stats.max_concentration}{_rate_str}", False, C_WHITE)
                self.screen.blit(conc_surf, (self._u(14), y))
                y += self._u(18)
                # Barra de Aljava
                from engine.components import Equipment as _EqHUD
                _eq_hud = self.world.get_component(self.player_entity, _EqHUD)
                _quiver = _eq_hud.slots.get("offhand") if _eq_hud else None
                if _quiver and getattr(_quiver, "item_type", "") == "quiver":
                    _arrow_ratio = _quiver.arrow_count / max(1, _quiver.max_arrows)
                    pygame.draw.rect(self.screen, (40, 30, 10),  (self._u(10), y, bar_w, bar_h))
                    pygame.draw.rect(self.screen, (200, 160, 60), (self._u(10), y, int(bar_w * _arrow_ratio), bar_h))
                    arrow_surf = self.font_sm.render(
                        f"Aljava {_quiver.arrow_count}/{_quiver.max_arrows}", False, C_WHITE)
                    self.screen.blit(arrow_surf, (self._u(14), y))
                else:
                    no_q_surf = self.font_sm.render("Sem aljava", False, (180, 130, 50))
                    self.screen.blit(no_q_surf, (self._u(14), y))
            else:
                # Vermelha (C_RED) — pedido do usuário 15/07/2026: era
                # laranja (C_ORANGE), confundia com a concentração do arqueiro.
                rage_ratio = char_stats.rage / max(1, char_stats.max_rage)
                pygame.draw.rect(self.screen, (60, 20, 0),   (self._u(10), y, bar_w, bar_h))
                pygame.draw.rect(self.screen, C_RED,          (self._u(10), y, int(bar_w * rage_ratio), bar_h))
                rage_surf = self.font_sm.render(
                    f"Raiva {char_stats.rage}/{char_stats.max_rage}", False, C_WHITE)
                self.screen.blit(rage_surf, (self._u(14), y))
            y += self._u(18)

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
