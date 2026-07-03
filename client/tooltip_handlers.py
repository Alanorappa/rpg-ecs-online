"""
tooltip_handlers.py — Mixin com geração e desenho de tooltips de
habilidades, itens (compare panel) e entidades do mundo: cálculo de
dano/alcance para exibição, montagem das linhas de texto e desenho
das caixas de tooltip. Separado de game.py para manter GameEngine
conciso. Esta classe NÃO deve ser instanciada diretamente — ela é
herdada por GameEngine, que fornece self.world, self._my_eid, self.screen
e os demais atributos referenciados aqui.
"""
import pygame

from client.colors import C_GREEN, C_RED, C_YELLOW
from components import CharacterStats, CombatStats, Position
from ui_compare import draw_compare_panel
from ui_helpers import wrap_text, fill_surf
from ui_sizes import UI


class TooltipHandlers:
    # ------------------------------------------------------------------
    # Helpers de tooltip de habilidades
    # ------------------------------------------------------------------

    def _player_spell_dmg(self, dmg_weapon_pct: float = 0.0,
                          sp_coeff: float = 1.0) -> int:
        """Calcula dano de magia usando a fórmula de _spell_damage (média de arma)."""
        cs = self.world.get_component(self.player_entity, CombatStats)
        if not cs:
            return 0
        from components import Equipment as _EqSpell
        equip  = self.world.get_component(self.player_entity, _EqSpell)
        weapon = equip.slots.get("mainhand") if equip else None
        if weapon and getattr(weapon, "damage_min", 0) > 0:
            weapon_avg = (weapon.damage_min + weapon.damage_max) / 2.0
        else:
            weapon_avg = float(cs.base_physical_damage)
        return max(1, int(weapon_avg * dmg_weapon_pct + cs.spell_power * sp_coeff))

    def _player_dmg_range(self, multiplier: float = 1.0) -> tuple[int, int]:
        """Retorna (min, max) de dano físico do jogador × multiplier."""
        cs = self.world.get_component(self.player_entity, CombatStats)
        if not cs:
            return (0, 0)
        from components import Equipment as _Eq
        equip = self.world.get_component(self.player_entity, _Eq)
        weapon = equip.slots.get("mainhand") if equip else None
        if weapon and weapon.damage_min > 0:
            base_min = cs.attack_power + weapon.damage_min
            base_max = cs.attack_power + weapon.damage_max
        else:
            base = cs.attack_power + cs.base_physical_damage
            base_min = base_max = base
        return (int(base_min * multiplier), int(base_max * multiplier))

    def _skill_tooltip_lines(self, skill, is_procced: bool, visual_ready: bool,
                              target_hp_ratio: float, player_rage: int) -> list:
        """Monta linhas de tooltip no formato: Dano / Cooldown / Status."""
        C_INFO   = (180, 180, 180)
        C_OK     = (80, 220, 80)
        C_WARN   = (220, 150, 50)
        C_RAGE   = (220, 100, 30)
        C_HEAL   = (80, 220, 150)

        sid = skill.skill_id
        lines = []

        # ── Dano / Efeito ────────────────────────────────────────────────
        if sid == "golpe_poderoso":
            lo, hi = self._player_dmg_range(3.0)
            lines.append((f"Dano: {lo}–{hi}  (3× dano físico)", C_INFO))

        elif sid == "impacto":
            lo, hi = self._player_dmg_range(0.5)
            lines.append((f"Dano: {lo}–{hi}  (50% dano físico — área 3 tiles)", C_INFO))

        elif sid == "executar":
            lo, hi = self._player_dmg_range(5.0)
            lines.append((f"Dano: {lo}–{hi}  (5× dano físico)", C_INFO))
            hp_txt = f"Alvo: {target_hp_ratio*100:.0f}% HP" if target_hp_ratio < 1.0 else "Alvo: —"
            lines.append((hp_txt, C_WARN if target_hp_ratio > 0.30 else C_OK))

        elif sid == "vitoria_iminente":
            cs = self.world.get_component(self.player_entity, CombatStats)
            cura = int(cs.max_hp * 0.30) if cs else 0
            lines.append((f"Cura: {cura} HP  (30% do HP máximo)", C_HEAL))
            lines.append((f"Cargas: {skill.charges}/{skill.max_charges}", C_INFO))

        elif sid == "interceptar":
            lines.append(("Dano: nenhum  (dash até o alvo)", C_INFO))

        elif sid == "golpe_debilitante":
            lo, hi = self._player_dmg_range(0.5)
            lines.append((f"Dano: {lo}–{hi}  (50% dano físico)", C_INFO))
            lines.append(("-50% velocidade do alvo por 5s", C_WARN))

        elif sid == "punho_no_queixo":
            cs = self.world.get_component(self.player_entity, CombatStats)
            dmg = int((cs.attack_power if cs else 0) * 0.45)
            lines.append((f"Dano: {dmg}  (45% do poder de ataque)", C_INFO))
            lines.append(("Atordoa o alvo por 3s", C_WARN))

        elif sid == "fatiador_de_corpos":
            lo, hi = self._player_dmg_range(0.45)
            lines.append((f"Dano: {lo}–{hi}/s por 5s  (45% dano físico — AoE)", C_INFO))

        elif sid == "brado_provocativo":
            lines.append(("Enlouquece inimigos em raio 3 tiles por 10s", C_WARN))
            lines.append(("+5% dano causado  /  +10% dano recebido", C_INFO))

        # ── Skills do Mago ───────────────────────────────────────────────
        elif sid == "bola_de_fogo":
            dmg = self._player_spell_dmg(0.5, 1.0)
            lines.append((f"Dano: ~{dmg}  (50% arma + 100% SP)", C_INFO))

        elif sid == "nova_congelante":
            dmg = self._player_spell_dmg(0.0, 0.5)
            lines.append((f"Dano: ~{dmg}  (50% SP)", C_INFO))
            lines.append(("Enraíza inimigos a 3 tiles por 5s", C_WARN))

        elif sid == "bloco_de_gelo":
            cs = self.world.get_component(self.player_entity, CombatStats)
            cura_s = int((cs.max_hp if cs else 0) * 0.10)
            lines.append(("Imunidade total por 5s  (imóvel)", C_WARN))
            lines.append((f"Regenera +{cura_s} HP/s  (10% HP máx)", C_HEAL))

        elif sid == "polimorfia":
            lines.append(("Transforma o alvo: perde controle", C_WARN))
            cs = self.world.get_component(self.player_entity, CombatStats)
            char = self.world.get_component(self.player_entity, CharacterStats)
            regen = int((cs.max_hp if cs else 0) * 0.10)
            lines.append((f"Alvo regenera +{regen} HP/s por 6s", C_INFO))
            lines.append(("Quebra ao receber dano", C_WARN))

        elif sid == "calamidade_flamejante":
            dmg = self._player_spell_dmg(0.15, 1.0)
            lines.append((f"Dano: ~{dmg}/s por 5s  (área 2 tiles)", C_INFO))
            lines.append(("-50% velocidade dos alvos por 5s", C_WARN))

        else:
            lines.append((skill.description, C_INFO))

        lines.append(("", C_INFO))  # separador

        # ── Custo de Mana ────────────────────────────────────────────────
        if skill.mana_cost > 0 or skill.mana_cost_pct > 0:
            _cs_m  = self.world.get_component(self.player_entity, CombatStats)
            _ch_m  = self.world.get_component(self.player_entity, CharacterStats)
            if skill.mana_cost_pct > 0 and _ch_m:
                eff_cost = max(1, int(_ch_m.max_mana * skill.mana_cost_pct))
            else:
                discount = getattr(_cs_m, "fire_mana_discount", 0) if _cs_m and sid == "bola_de_fogo" else 0
                eff_cost = max(0, skill.mana_cost - discount)
            cur_mana = int(_ch_m.mana) if _ch_m else 0
            max_mana = int(_ch_m.max_mana) if _ch_m else 0
            lines.append((f"Mana: {eff_cost}  (atual: {cur_mana}/{max_mana})",
                           C_OK if cur_mana >= eff_cost else C_RAGE))

        # ── Cast time ────────────────────────────────────────────────────
        if skill.cast_time > 0:
            _cs_ct = self.world.get_component(self.player_entity, CombatStats)
            if sid in ("bola_de_fogo", "polimorfia", "calamidade_flamejante"):
                red = getattr(_cs_ct, "fire_cast_time_reduction", 0.0) if _cs_ct else 0.0
            elif sid == "nova_congelante":
                red = getattr(_cs_ct, "ice_cast_time_reduction", 0.0) if _cs_ct else 0.0
            else:
                red = 0.0
            eff_cast = max(0.0, skill.cast_time - red)
            if eff_cast == 0.0:
                lines.append(("Cast: instantâneo  (talento)", C_OK))
            else:
                lines.append((f"Cast: {eff_cast:.1f}s", C_INFO))

        # ── Cooldown / Custo de Raiva ─────────────────────────────────────
        if skill.rage_cost > 0:
            cost = skill.rage_cost
            if sid == "golpe_poderoso":
                _cs_tt = self.world.get_component(self.player_entity, CombatStats)
                cost = _cs_tt.golpe_poderoso_rage_cost if _cs_tt else cost
            lines.append((f"Custo: {cost} Raiva  (atual: {player_rage})",
                           C_OK if player_rage >= cost else C_RAGE))

        elif sid == "interceptar":
            _cs_tt = self.world.get_component(self.player_entity, CombatStats)
            cd_ef = max(0.0, skill.cooldown - (_cs_tt.interceptar_cooldown_reduction if _cs_tt else 0.0))
            lines.append((f"Cooldown: {cd_ef:.0f}s", C_INFO))

        elif skill.cooldown > 0:
            lines.append((f"Cooldown: {skill.cooldown:.0f}s", C_INFO))

        # ── Status ───────────────────────────────────────────────────────
        if sid == "executar":
            if is_procced:
                status = ("Status: PRONTO — execute!", C_OK)
            elif target_hp_ratio <= 0.30 and target_hp_ratio > 0:
                status = ("Status: Raiva insuficiente", C_RAGE)
            elif target_hp_ratio < 1.0:
                status = ("Status: Alvo com HP alto", C_WARN)
            elif not visual_ready:
                status = (f"Status: Em recarga  {skill.current_cooldown:.1f}s", C_WARN)
            else:
                status = ("Status: Aguardando alvo < 30% HP", C_WARN)

        elif skill.max_charges > 0:
            if is_procced:
                status = ("Status: Carga disponível!", C_OK)
            else:
                status = ("Status: Sem cargas  (mate um inimigo)", C_WARN)

        elif skill.rage_cost > 0:
            cost6 = skill.rage_cost
            if sid == "golpe_poderoso":
                _cs_tt6 = self.world.get_component(self.player_entity, CombatStats)
                cost6 = _cs_tt6.golpe_poderoso_rage_cost if _cs_tt6 else cost6
            if player_rage < cost6 and not (is_procced and skill.proc_ignores_cost):
                status = ("Status: Raiva insuficiente", C_RAGE)
            elif visual_ready:
                # Totalmente utilizável (proc + todas condições)
                if is_procced:
                    status = ("Status: PRONTO — proc ativo!", C_OK)
                else:
                    status = ("Status: Pronto", C_OK)
            elif is_procced:
                # Proc existe mas falta alvo ou outra condição
                if sid == "executar":
                    from components import CharacterStats as _CStt
                    _ch_tt = self.world.get_component(self.player_entity, _CStt)
                    if _ch_tt and getattr(_ch_tt, "free_executar_charges", 0) > 0:
                        status = ("Status: Assassino — selecione um alvo", C_WARN)
                    else:
                        status = ("Status: Alvo fraco — aproxime-se", C_WARN)
                else:
                    status = ("Status: Proc ativo — condição incompleta", C_WARN)
            elif not visual_ready:
                status = (f"Status: Em recarga  {skill.current_cooldown:.1f}s", C_WARN)
            else:
                status = ("Status: Pronto", C_OK)

        else:
            if visual_ready:
                status = ("Status: Pronto", C_OK)
            else:
                status = (f"Status: Em recarga  {skill.current_cooldown:.1f}s", C_WARN)

        lines.append(status)
        return lines

    def _draw_tooltip(self, mx: int, my: int, title: str, lines: list,
                      title_color=(255, 220, 100),
                      body_font: "pygame.font.Font | None" = None) -> pygame.Rect:
        """Renderiza caixa de tooltip próxima ao cursor. Retorna o rect desenhado.
        Cada entrada de `lines` pode ser:
          (text, color)                                    -- linha simples
          ((left_text, left_color), (right_text, right_color))  -- duas colunas
        body_font: fonte para as linhas de conteúdo (default: self.font_sm).
        """
        bf        = body_font or self.font_sm
        PAD       = self._u(UI.TOOLTIP_PAD)
        LINE_H    = bf.get_height() + self._u(UI.TOOLTIP_LINE_EXTRA)
        COL_GAP   = self._u(UI.TOOLTIP_COL_GAP)   # espaço mínimo entre coluna esquerda e direita
        MAX_W     = self._u(UI.TOOLTIP_MAX_W)
        max_text_w = MAX_W - PAD * 2

        t_surf = self.font_md.render(title, True, title_color)

        # Pré-renderizar todas as linhas. Linhas de uma coluna ("one") que
        # excedem MAX_W quebram em múltiplas sub-linhas (word-wrap) — sem
        # isso, uma descrição longa (ex: skill.description cru, usado como
        # fallback) virava uma única linha gigante e a caixa toda esticava
        # pra acompanhar (ver PROBLEMAS_ARQUITETURA.md item IU4).
        rendered = []
        for line in lines:
            if isinstance(line[0], tuple):
                (lt, lc), (rt, rc) = line
                rendered.append(("two", bf.render(lt, True, lc),
                                         bf.render(rt, True, rc)))
            else:
                t, c = line
                if t and bf.size(t)[0] > max_text_w:
                    for sub in wrap_text(t, bf, max_text_w):
                        rendered.append(("one", bf.render(sub, True, c)))
                else:
                    rendered.append(("one", bf.render(t, True, c)))

        def _line_w(r):
            if r[0] == "two":
                return r[1].get_width() + COL_GAP + r[2].get_width()
            return r[1].get_width()

        content_w = max(((_line_w(r)) for r in rendered), default=0)
        tw = max(t_surf.get_width(), content_w) + PAD * 2
        th = self.font_md.get_height() + len(rendered) * LINE_H + PAD * 2 + self._u(4)

        tx = mx + self._u(14)
        ty = my - th - self._u(4)
        if tx + tw > self.screen.get_width():
            tx = mx - tw - self._u(4)
        ty = max(0, min(ty, self.screen.get_height() - th))

        self.screen.blit(fill_surf((tw, th), (10, 8, 5, 220)), (tx, ty))
        pygame.draw.rect(self.screen, (120, 90, 50), (tx, ty, tw, th), 1, border_radius=3)
        self.screen.blit(t_surf, (tx + PAD, ty + PAD))
        for i, r in enumerate(rendered):
            y = ty + PAD + self.font_md.get_height() + self._u(4) + i * LINE_H
            if r[0] == "two":
                self.screen.blit(r[1], (tx + PAD, y))
                self.screen.blit(r[2], (tx + tw - PAD - r[2].get_width(), y))
            else:
                self.screen.blit(r[1], (tx + PAD, y))
        return pygame.Rect(tx, ty, tw, th)

    def _flush_tooltip(self):
        if not self._pending_tooltip:
            return
        # _pending_tooltip: (mx, my, title, lines[, title_color | new_item, equipped_item])
        # Elemento 4 pode ser:
        #   - tuple de 3 ints  → cor do título (mob tooltip com cor de tier)
        #   - Item object      → item para comparação (shop/bag tooltip)
        mx, my, title, lines = self._pending_tooltip[:4]
        rest = self._pending_tooltip[4:]
        title_color = (255, 220, 100)
        if rest and isinstance(rest[0], tuple):
            title_color = rest[0]
            rest = rest[1:]
        tip_rect = self._draw_tooltip(mx, my, title, lines, title_color)
        # Shift + hover com dados de comparação → dois painéis ao lado do tooltip
        if (len(rest) >= 2
                and pygame.key.get_mods() & pygame.KMOD_SHIFT):
            new_item, eq_item = rest[0], rest[1]
            draw_compare_panel(self.screen, self.font_sm, self.font_md,
                               new_item, eq_item, tip_rect)

    def _flush_skill_tooltip(self):
        """Renderiza tooltip de habilidade com a mesma escala do tooltip de itens."""
        if not self._pending_skill_tooltip:
            return
        mx, my, title, lines = self._pending_skill_tooltip[:4]
        self._draw_tooltip(mx, my, title, lines, body_font=self.font_sm)

    def _draw_world_tooltip(self):
        """Mostra tooltip fixo no canto inferior direito ao passar o mouse sobre NPCs/mobs."""
        from components import Enemy, EnemyTier, Renderable, Merchant, QuestGiver, NPC, Visible, EntityIdentity
        mx, my = pygame.mouse.get_pos()
        z  = self._zoom
        wx = mx / z + self._cam_x
        wy = my / z + self._cam_y

        title      = None
        lines      = []
        title_col  = (255, 220, 100)

        # NPCs — qualquer entidade com componente NPC (Merchant, QuestGiver, futuro Trainer...)
        for eid, pos, rend, npc, _ in self.world.get_entities_with(
                Position, Renderable, NPC, Visible):
            hw, hh = rend.width / 2, rend.height / 2
            if not (pos.x - hw <= wx <= pos.x + hw and pos.y - hh <= wy <= pos.y + hh):
                continue
            title = npc.name
            lines = [(f"Nivel {npc.level}  {npc.profession}", (160, 160, 160))]
            # Dica de interação baseada nas capacidades do NPC
            has_shop  = self.world.get_component(eid, Merchant)  is not None
            has_quest = self.world.get_component(eid, QuestGiver) is not None
            if has_shop and has_quest:
                lines.append(("Clique direito para interagir", (220, 200, 100)))
            elif has_shop:
                lines.append(("Clique direito para abrir a loja", (150, 220, 150)))
            elif has_quest:
                lines.append(("Clique direito para interagir", (220, 200, 100)))
            break

        # Inimigos
        if title is None:
            tier_colors = {"normal": (200, 200, 200), "elite": (140, 140, 255),
                           "rare": (255, 165, 0), "boss": (220, 80, 220)}
            for eid, pos, rend, _, _ in self.world.get_entities_with(
                    Position, Renderable, Enemy, Visible):
                cs = self.world.get_component(eid, CombatStats)
                if cs and cs.current_hp <= 0:
                    continue
                hw, hh = rend.width / 2, rend.height / 2
                if not (pos.x - hw <= wx <= pos.x + hw and pos.y - hh <= wy <= pos.y + hh):
                    continue
                tier_c    = self.world.get_component(eid, EnemyTier)
                ident     = self.world.get_component(eid, EntityIdentity)
                tier      = tier_c.tier if tier_c else "normal"
                title     = ident.name  if ident else "Inimigo"
                mob_race  = ident.race  if ident else "?"
                mob_level = ident.level if ident else 1
                title_col = tier_colors.get(tier.lower(), (200, 200, 200))
                lines = [(f"Level {mob_level}  {mob_race}", (160, 160, 160))]
                if cs:
                    hp_pct = int(cs.current_hp / max(1, cs.max_hp) * 100)
                    lines.append((f"HP: {cs.current_hp}/{cs.max_hp} ({hp_pct}%)",
                                  C_GREEN if hp_pct > 50 else C_YELLOW if hp_pct > 25 else C_RED))
                else:
                    # Mob remoto — HP em RemoteEntityMeta
                    _meta_tt = self._meta_from_local(eid)
                    if _meta_tt and _meta_tt.hp_max > 0:
                        hp_pct = int(_meta_tt.hp / _meta_tt.hp_max * 100)
                        lines.append((f"HP: {_meta_tt.hp}/{_meta_tt.hp_max} ({hp_pct}%)",
                                      C_GREEN if hp_pct > 50 else C_YELLOW if hp_pct > 25 else C_RED))
                break

        if title is None:
            return

        # Renderizar tooltip fixo no canto inferior direito
        PAD    = 10
        LINE_H = self.font_sm.get_height() + 3
        t_surf = self.font_md.render(title, True, title_col)
        line_surfs = [self.font_sm.render(l[0], True, l[1]) for l in lines]
        tw = max(t_surf.get_width(), *(s.get_width() for s in line_surfs)) + PAD * 2
        th = self.font_md.get_height() + len(line_surfs) * LINE_H + PAD * 2 + 4
        tx = self.screen.get_width()  - tw  - 12
        ty = self.screen.get_height() - th  - 12
        self.screen.blit(fill_surf((tw, th), (10, 8, 5, 210)), (tx, ty))
        pygame.draw.rect(self.screen, (120, 90, 50), (tx, ty, tw, th), 1, border_radius=3)
        self.screen.blit(t_surf, (tx + PAD, ty + PAD))
        for i, s in enumerate(line_surfs):
            self.screen.blit(s, (tx + PAD, ty + PAD + self.font_md.get_height() + 4 + i * LINE_H))
