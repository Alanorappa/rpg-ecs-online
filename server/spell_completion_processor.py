"""
server/spell_completion_processor.py
Mixin para WorldServer: processa conclusão de spells com cast_time no servidor.

Fluxo:
  1. skill_processor.py expõe _pending_spell_completions ao SkillSystem
  2. Handlers de spells com cast_time adicionam entrada em vez de criar SpellCast
  3. _process_spell_cast_completions(dt) decrementa timers e dispara efeitos
  4. Resultado adicionado a _skill_results_this_tick → enviado pelo session.py
"""
from __future__ import annotations
import math
import random


class SpellCompletionMixin:

    def _process_spell_cast_completions(self, dt: float) -> None:
        """Avança timers de spells pendentes e dispara efeitos quando concluídas."""
        from components import CombatStats as _CS, CharacterStats as _CHS, StatusEffects as _SFX

        still = []
        for entry in self._pending_spell_completions:
            entry["timer"] -= dt
            if entry["timer"] > 0:
                still.append(entry)
                continue

            player_eid = entry["player_eid"]
            spell_id   = entry["spell_id"]
            target_id  = entry.get("target_id", -1)
            mana_cost  = entry.get("mana_cost", 0)

            # Cobra mana ao completar (foi validada mas não deduzida no handler)
            char = self.world.get_component(player_eid, _CHS)
            if char and mana_cost > 0:
                char.mana = max(0, char.mana - mana_cost)

            # HP snapshot de todos os mobs antes de aplicar efeito
            hp_before  = {}
            sfx_before = {}
            for mob_eid in self._mob_eids:
                _cs = self.world.get_component(mob_eid, _CS)
                if _cs:
                    hp_before[mob_eid] = _cs.current_hp
                _sfx = self.world.get_component(mob_eid, _SFX)
                sfx_before[mob_eid] = set(_sfx.effects.keys()) if _sfx else set()

            _dispatch = {
                "bola_de_fogo":    self._server_bola_de_fogo,
                "nova_congelante": self._server_nova_congelante,
                "polimorfia":      self._server_polimorfia,
                "calcinar":        self._server_calcinar,
            }
            fn = _dispatch.get(spell_id)
            if fn:
                try:
                    fn(player_eid, target_id, entry)
                except Exception as _err:
                    import traceback
                    print(f"[SpellCompletion] ERRO {spell_id}: {_err}")
                    traceback.print_exc()

            # Coleta dano e efeitos aplicados
            results = []
            for mob_eid, hp_pre in hp_before.items():
                _cs2 = self.world.get_component(mob_eid, _CS)
                if not _cs2:
                    continue
                hp_real  = _cs2.current_hp
                hp_after = max(0, hp_real)
                damage   = max(0, hp_pre - hp_real)
                _sfx2    = self.world.get_component(mob_eid, _SFX)
                eff_now  = set(_sfx2.effects.keys()) if _sfx2 else set()
                applied  = list(eff_now - sfx_before.get(mob_eid, set()))
                if damage > 0 or applied:
                    results.append({
                        "eid":             mob_eid,
                        "damage":          damage,
                        "outcome":         "hit",
                        "hp_after":        hp_after,
                        "applied_effects": applied,
                    })

            # SKILL_RESULT da conclusão do cast — inclui cooldown=None (já aplicado no cast start)
            skill_entry: dict = {
                "caster_eid": player_eid,
                "sid":        spell_id,
                "targets":    results,
                "cooldown":   None,
                "failed":     False,
            }

            # Chama Interna: sincroniza proc ao cliente via SKILL_RESULT
            if char and getattr(char, "fire_instant_ready", False):
                skill_entry["fire_instant_proc"] = True

            self._skill_results_this_tick.append(skill_entry)

            # Sincroniza mana ao cliente
            _cs_after = self.world.get_component(player_eid, _CS)
            if char:
                self._pending_xp_deliveries.append({
                    "player_eid": player_eid,
                    "xp":         0,
                    "mob_eid":    -1,
                    "rage":       char.rage,
                    "mana":       char.mana,
                })

        self._pending_spell_completions = still

    # ── Bloco de Gelo: timer server-side ────────────────────────────────────

    def _process_ice_blocks(self, dt: float) -> None:
        """Avança o timer de Bloco de Gelo e limpa is_immune/is_stunned ao expirar."""
        from components import IceBlockEffect as _IBE, CombatState as _CSt, CombatStats as _CS

        to_clear = []
        for player_eid in list(self._player_eids.values()):
            ibe = self.world.get_component(player_eid, _IBE)
            if ibe is None:
                continue
            ibe.elapsed   += dt
            ibe.last_heal += dt

            cs  = self.world.get_component(player_eid, _CS)
            cst = self.world.get_component(player_eid, _CSt)

            # Cura 10% HP/s
            if ibe.last_heal >= ibe.heal_interval and cs:
                ibe.last_heal -= ibe.heal_interval
                heal = max(1, int(cs.max_hp * 0.10))
                old_hp = cs.current_hp
                cs.current_hp = min(cs.max_hp, cs.current_hp + heal)
                if cs.current_hp != old_hp:
                    self._player_hp_broadcasts_this_tick.append({
                        "eid": player_eid, "hp": cs.current_hp, "hp_max": cs.max_hp,
                    })

            if ibe.elapsed >= ibe.duration:
                to_clear.append((player_eid, cst))

        for player_eid, cst in to_clear:
            try:
                self.world.remove_component(player_eid, _IBE)
            except Exception:
                pass
            if cst:
                cst.is_stunned = False
                cst.is_immune  = False

    # ── Dano de magia server-side (sem pygame) ───────────────────────────────

    def _server_spell_damage(self, player_eid: int, dmg_weapon_pct: float, sp_coeff: float) -> int:
        from components import CombatStats, Equipment
        cs = self.world.get_component(player_eid, CombatStats)
        eq = self.world.get_component(player_eid, Equipment)
        if not cs:
            return 1
        weapon_dmg = float(cs.base_physical_damage)
        if eq:
            wep = eq.slots.get("mainhand")
            if wep and getattr(wep, "damage_min", 0) and getattr(wep, "damage_max", 0):
                weapon_dmg = (wep.damage_min + wep.damage_max) / 2.0
        return max(1, int(weapon_dmg * dmg_weapon_pct + cs.spell_power * sp_coeff))

    def _server_apply_magic_damage(self, attacker_id: int, target_id: int,
                                   dmg: int, is_crit: bool = False) -> bool:
        """Aplica dano mágico server-side (sem FLT/WARN/pygame)."""
        from components import (CombatStats, CombatState, AIControlled,
                                MobSounds, PendingDeath, StatusEffects)
        from stat_fns import enter_combat

        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return False
        target_state = self.world.get_component(target_id, CombatState)
        if target_state and target_state.is_immune:
            return False

        target_cs.current_hp = max(0, target_cs.current_hp - dmg)

        # Quebra polimorfia
        _t_sfx = self.world.get_component(target_id, StatusEffects)
        if _t_sfx:
            _t_sfx.remove("polymorph")

        attacker_state = self.world.get_component(attacker_id, CombatState)
        if attacker_state:
            enter_combat(attacker_state)

        _ai = self.world.get_component(target_id, AIControlled)
        if _ai and _ai.state in ("IDLE", "RETURNING"):
            try:
                from sound_manager import SOUNDS
                _ms = self.world.get_component(target_id, MobSounds)
                SOUNDS.play_mob_sounds(_ms, "aggro", dedup_key=f"dmg_{target_id}")
            except Exception:
                pass
            _ai.state             = "CHASING"
            _ai.aggroed_by_damage = True
            _ai.path_recalc_timer = 0.0

        if target_cs.current_hp <= 0:
            if not self.world.get_component(target_id, PendingDeath):
                self.world.add_component(target_id, PendingDeath(killer_entity_id=attacker_id))
            return True
        return False

    # ── Handlers de conclusão de cada spell ──────────────────────────────────

    def _server_bola_de_fogo(self, player_eid: int, target_id: int, entry: dict) -> None:
        from components import CombatStats, StatusEffects, CharacterStats
        from systems import apply_effect
        from damage_calculator import CRITICAL_DAMAGE_MULTIPLIER, resolve_attack_outcome

        if target_id == -1:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return

        player_cs  = self.world.get_component(player_eid, CombatStats)
        char_stats = self.world.get_component(player_eid, CharacterStats)

        base_dmg = self._server_spell_damage(player_eid, 0.5, 1.0)

        outcome, _ = resolve_attack_outcome(player_cs, target_cs, "magical")
        is_crit    = (outcome == "crit")
        final_dmg  = int(base_dmg * CRITICAL_DAMAGE_MULTIPLIER) if is_crit else base_dmg

        if player_cs:
            _pyr = getattr(player_cs, "pyromania_bonus", 0.0)
            if _pyr > 0:
                final_dmg = int(final_dmg * (1.0 + _pyr))
            if getattr(player_cs, "crematoria_enabled", False):
                if target_cs.max_hp > 0 and target_cs.current_hp / target_cs.max_hp < 0.20:
                    final_dmg = int(final_dmg * 1.25)
            if getattr(player_cs, "thermal_shock_enabled", False):
                _sfx = self.world.get_component(target_id, StatusEffects)
                if _sfx and _sfx.has("root"):
                    final_dmg = int(final_dmg * 2.0)

        self._server_apply_magic_damage(player_eid, target_id, final_dmg, is_crit)

        # Queimaduras Profundas
        if is_crit and player_cs and getattr(player_cs, "fire_burns_on_crit", False):
            burn_dmg = max(1, int(player_cs.spell_power * 0.3))
            burn_dur = getattr(player_cs, "fire_burn_duration", 3.0)
            apply_effect(self.world, target_id, "burn", burn_dur, burn_dmg)

        # Lapso Elemental: conta crits de fogo
        if is_crit and player_cs:
            player_cs.fire_crit_counter = getattr(player_cs, "fire_crit_counter", 0) + 1
            player_cs.fire_crit_timer   = 6.0
            _lapse = getattr(player_cs, "elemental_lapse_crit_bonus", 0.0)
            if getattr(player_cs, "fire_crit_counter", 0) >= 3 and _lapse > 0:
                player_cs.fire_crit_counter = 0
                player_cs.fire_crit_timer   = 0.0
                apply_effect(self.world, player_eid, "elemental_lapse", 5.0, 0)
                from components import Modifier
                from stat_fns import add_timed_modifier
                add_timed_modifier(player_cs, Modifier("crit_rating", _lapse, "flat"), 5.0, "lapso_elemental")

        # Exaustão: slow progressivo por BdF consecutiva
        if player_cs and getattr(player_cs, "fire_exhaustion_enabled", False):
            from components import ActiveEffect as _AEX, StatusEffects as _SFX2
            _t_sfx = self.world.get_component(target_id, _SFX2)
            if _t_sfx is None:
                _t_sfx = _SFX2()
                self.world.add_component(target_id, _t_sfx)
            _exh = _t_sfx.get("exhaustion")
            if _exh:
                _new_stacks = min(_exh.magnitude + 1, 5)
                _exh.magnitude = _new_stacks
                _exh.duration  = 6.0
            else:
                _new_stacks = 1
                _t_sfx.effects["exhaustion"] = _AEX("exhaustion", 6.0, 1, 0.0)
            _slow_pct = (_new_stacks - 1) * 0.05
            if _slow_pct > 0:
                _slow = _t_sfx.get("slow")
                if _slow:
                    _slow.magnitude = min(_slow.magnitude, 1.0 - _slow_pct)
                    _slow.duration  = 6.0
                else:
                    _t_sfx.effects["slow"] = _AEX("slow", 6.0, 1.0 - _slow_pct, 0.0)

        # Chama Interna: proc após hit de spell de fogo
        if player_cs and char_stats:
            _proc_chance = getattr(player_cs, "fire_instant_proc_chance", 0.0)
            if _proc_chance > 0 and random.random() < _proc_chance:
                if not char_stats.fire_instant_ready:
                    char_stats.fire_instant_ready = True

    def _server_calcinar(self, player_eid: int, target_id: int, entry: dict) -> None:
        from components import CombatStats, StatusEffects, CharacterStats

        if target_id == -1:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return

        player_cs  = self.world.get_component(player_eid, CombatStats)
        char_stats = self.world.get_component(player_eid, CharacterStats)
        sp = player_cs.spell_power if player_cs else 0

        base_dmg = max(1, 50 + int(sp * 0.25))
        if player_cs:
            _pyr = getattr(player_cs, "pyromania_bonus", 0.0)
            if _pyr > 0:
                base_dmg = int(base_dmg * (1.0 + _pyr))
            if getattr(player_cs, "crematoria_enabled", False):
                if target_cs.max_hp > 0 and target_cs.current_hp / target_cs.max_hp < 0.20:
                    base_dmg = int(base_dmg * 1.25)
            if getattr(player_cs, "thermal_shock_enabled", False):
                _sfx = self.world.get_component(target_id, StatusEffects)
                if _sfx and _sfx.has("root"):
                    base_dmg = int(base_dmg * 2.0)

        self._server_apply_magic_damage(player_eid, target_id, base_dmg)

        # Chama Interna: proc após hit de fogo
        if player_cs and char_stats:
            _proc_chance = getattr(player_cs, "fire_instant_proc_chance", 0.0)
            if _proc_chance > 0 and random.random() < _proc_chance:
                if not char_stats.fire_instant_ready:
                    char_stats.fire_instant_ready = True

    def _server_nova_congelante(self, player_eid: int, target_id: int, entry: dict) -> None:
        from components import CombatStats, TileMovement, Enemy, AIControlled
        from systems import apply_effect
        from utils import chebyshev

        tm_p = self.world.get_component(player_eid, TileMovement)
        cs_p = self.world.get_component(player_eid, CombatStats)
        if not tm_p:
            return

        pl_x = tm_p.current_tile_x
        pl_y = tm_p.current_tile_y
        sp   = cs_p.spell_power if cs_p else 0

        for eid, _, _, etm, ecs in self.world.get_entities_with(
                Enemy, AIControlled, TileMovement, CombatStats):
            if ecs.current_hp <= 0:
                continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) > 3:
                continue
            dmg = max(1, int(sp * 0.5))
            self._server_apply_magic_damage(player_eid, eid, dmg)
            apply_effect(self.world, eid, "root", 5.0)

    def _server_polimorfia(self, player_eid: int, target_id: int, entry: dict) -> None:
        from components import CombatStats, CombatState
        from systems import apply_effect

        if target_id == -1:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return

        regen_per_tick = max(1, int(target_cs.max_hp * 0.10))
        apply_effect(self.world, target_id, "polymorph", duration=6.0, magnitude=regen_per_tick)

        attacker_state = self.world.get_component(player_eid, CombatState)
        if attacker_state:
            attacker_state.is_pursuing = False
