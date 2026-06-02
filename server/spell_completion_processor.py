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

            # Spells com projétil: aguardam PROJECTILE_HIT_CS antes de aplicar dano
            _PROJECTILE_SPELLS = {"bola_de_fogo"}
            _dispatch = {
                "nova_congelante": self._server_nova_congelante,
                "polimorfia":      self._server_polimorfia,
                "calcinar":        self._server_calcinar,
            }
            from server.spell_debug_log import splog as _splog2
            if spell_id in _PROJECTILE_SPELLS:
                import time as _t_if
                self._spells_in_flight_queue.append({
                    "player_eid": player_eid,
                    "spell_id":   spell_id,
                    "target_id":  target_id,
                    "entry":      entry,
                    "expires_at": _t_if.time() + 2.0,
                })
                _splog2(f"COMPLETION {spell_id} player={player_eid} target={target_id} → em voo")
            else:
                fn = _dispatch.get(spell_id)
                _tcs_pre = self.world.get_component(target_id, _CS)
                _splog2(f"COMPLETION {spell_id} player={player_eid} target={target_id} "
                        f"target_alive={_tcs_pre is not None and _tcs_pre.current_hp > 0} "
                        f"mobs_tracked={len(self._mob_eids)}")
                if fn:
                    try:
                        fn(player_eid, target_id, entry)
                    except Exception as _err:
                        import traceback
                        _splog2(f"  ERRO: {_err}")
                        print(f"[SpellCompletion] ERRO {spell_id}: {_err}")
                        traceback.print_exc()

            # Coleta dano e efeitos aplicados (apenas para spells sem projétil)
            results = []
            for mob_eid, hp_pre in hp_before.items():
                _cs2 = self.world.get_component(mob_eid, _CS)
                if not _cs2:
                    continue
                hp_real  = _cs2.current_hp
                hp_after = max(0, hp_real)
                damage   = max(0, hp_pre - hp_real)
                if damage > 0:
                    _splog2(f"  dano mob={mob_eid} dmg={damage} hp_pre={hp_pre} hp_now={hp_after}")
                _sfx2    = self.world.get_component(mob_eid, _SFX)
                eff_now  = set(_sfx2.effects.keys()) if _sfx2 else set()
                applied  = list(eff_now - sfx_before.get(mob_eid, set()))
                if damage > 0 or applied:
                    _res = {
                        "eid":             mob_eid,
                        "damage":          damage,
                        "outcome":         "hit",
                        "hp_after":        hp_after,
                        "applied_effects": applied,
                    }
                    # Sincroniza slow_mult ao cliente para evitar desync visual do mob
                    if _sfx2:
                        _slow_eff = _sfx2.get("slow")
                        if _slow_eff:
                            _res["mob_slow_mult"] = _slow_eff.magnitude
                    results.append(_res)

            # SKILL_RESULT da conclusão do cast — toca som e aplica cooldown (GCD já foi).
            skill_entry: dict = {
                "caster_eid":   player_eid,
                "sid":          spell_id,
                "targets":      results,
                "cooldown":     None,
                "failed":       False,
                "is_completion": True,
                # Para spells com projétil: informa o alvo para o cliente criar o projétil
                "projectile_target": target_id if spell_id in {"bola_de_fogo"} else -1,
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

    # ── Projéteis em voo: aguardando PROJECTILE_HIT_CS ──────────────────────

    def _expire_spells_in_flight(self) -> None:
        """Remove entradas de _spells_in_flight_queue que expiraram (projétil nunca chegou)."""
        import time as _t_exp
        now = _t_exp.time()
        self._spells_in_flight_queue = [
            e for e in self._spells_in_flight_queue if e["expires_at"] > now
        ]

    def _apply_spell_on_projectile_hit(self, player_eid: int, spell_id: str,
                                        target_id: int) -> None:
        """Chamado ao receber PROJECTILE_HIT_CS: aplica dano e envia SKILL_RESULT."""
        from components import CombatStats as _CS, CharacterStats as _CHS, StatusEffects as _SFX

        # Localiza a entrada em voo correspondente
        entry = None
        for i, e in enumerate(self._spells_in_flight_queue):
            if (e["player_eid"] == player_eid and e["spell_id"] == spell_id
                    and e["target_id"] == target_id):
                entry = e
                del self._spells_in_flight_queue[i]
                break
        if entry is None:
            return  # expirou ou nunca foi enfileirado

        _dispatch = {"bola_de_fogo": self._server_bola_de_fogo}
        fn = _dispatch.get(spell_id)
        if not fn:
            return

        # Snapshot HP antes
        hp_before: dict[int, int] = {}
        sfx_before: dict[int, set] = {}
        for mob_eid in self._mob_eids:
            _cs = self.world.get_component(mob_eid, _CS)
            if _cs:
                hp_before[mob_eid] = _cs.current_hp
            _sfx = self.world.get_component(mob_eid, _SFX)
            sfx_before[mob_eid] = set(_sfx.effects.keys()) if _sfx else set()

        self._last_proj_spell_is_crit = False
        self._last_proj_lapso_proc    = None
        try:
            fn(player_eid, target_id, entry["entry"])
        except Exception as _err:
            import traceback
            print(f"[ProjHit] ERRO {spell_id}: {_err}")
            traceback.print_exc()
            return

        _proj_is_crit  = getattr(self, "_last_proj_spell_is_crit", False)
        _lapso_proc    = getattr(self, "_last_proj_lapso_proc",    None)

        # Coleta dano
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
                _res2 = {
                    "eid":             mob_eid,
                    "damage":          damage,
                    "outcome":         "crit" if _proj_is_crit else "hit",
                    "hp_after":        hp_after,
                    "applied_effects": applied,
                }
                if _sfx2:
                    _slow2 = _sfx2.get("slow")
                    if _slow2:
                        _res2["mob_slow_mult"] = _slow2.magnitude
                results.append(_res2)

        char = self.world.get_component(player_eid, _CHS)
        skill_entry: dict = {
            "caster_eid":    player_eid,
            "sid":           spell_id,
            "targets":       results,
            "cooldown":      None,
            "failed":        False,
            "is_proj_damage": True,  # só mostra dano — GCD/CD/som já foram em is_completion
        }
        if char and getattr(char, "fire_instant_ready", False):
            skill_entry["fire_instant_proc"] = True
        if _lapso_proc:
            skill_entry["lapso_proc"] = _lapso_proc

        self._skill_results_this_tick.append(skill_entry)

        # Sincroniza mana
        if char:
            self._pending_xp_deliveries.append({
                "player_eid": player_eid,
                "xp":         0,
                "mob_eid":    -1,
                "rage":       char.rage,
                "mana":       char.mana,
            })

    # ── Channeling de players server-side (Calamidade Flamejante) ───────────

    def _process_player_channeling(self, dt: float) -> None:
        """Processa ticks de canalização de players (Calamidade Flamejante)."""
        from components import Channeling as _Chan, CombatStats as _CS, \
                               CharacterStats as _CHS, TileMovement as _TM
        from systems import apply_effect
        from utils import chebyshev

        _to_remove = []
        for player_eid in list(self._player_eids.values()):
            ch = self.world.get_component(player_eid, _Chan)
            if ch is None:
                continue

            ch.elapsed += dt
            if ch.elapsed >= ch.duration:
                _to_remove.append(player_eid)
                continue

            # Deduz mana por tick
            char = self.world.get_component(player_eid, _CHS)
            if char and ch.mana_per_tick > 0:
                ch.mana_timer = getattr(ch, "mana_timer", 0.0) + dt
                if ch.mana_timer >= ch.tick_interval:
                    ch.mana_timer -= ch.tick_interval
                    char.mana = max(0, char.mana - ch.mana_per_tick)
                    if char.mana == 0:
                        _to_remove.append(player_eid)
                        continue

            # Tick de dano
            if not hasattr(ch, "tick_timer"):
                ch.tick_timer = ch.tick_interval
            ch.tick_timer -= dt
            if ch.tick_timer <= 0:
                ch.tick_timer += ch.tick_interval
                cs_p = self.world.get_component(player_eid, _CS)
                sp   = cs_p.spell_power if cs_p else 0
                tx   = int(ch.target_x / 32)
                ty   = int(ch.target_y / 32)

                for mob_eid in list(self._mob_eids):
                    mob_tm = self.world.get_component(mob_eid, _TM)
                    mob_cs = self.world.get_component(mob_eid, _CS)
                    if not mob_tm or not mob_cs or mob_cs.current_hp <= 0:
                        continue
                    if chebyshev(tx, ty, mob_tm.current_tile_x, mob_tm.current_tile_y) > ch.radius_tiles:
                        continue
                    dmg = max(1, int(cs_p.base_physical_damage * ch.dmg_weapon_pct
                                     + sp * ch.dmg_sp_coeff)) if cs_p else 1
                    hp_before = mob_cs.current_hp
                    self._server_apply_magic_damage(player_eid, mob_eid, dmg)
                    hp_after  = max(0, mob_cs.current_hp)
                    damage    = max(0, hp_before - mob_cs.current_hp)
                    if damage > 0:
                        # Envia COMBAT_RESULT ao cliente para exibir FLT
                        self._combat_this_tick.append({
                            "attacker": player_eid,
                            "target":   mob_eid,
                            "damage":   damage,
                            "outcome":  "hit",
                            "hp_after": hp_after,
                            "source":   "skill",
                        })
                    if ch.slow_pct > 0:
                        apply_effect(self.world, mob_eid, "slow", 2.0, 1.0 - ch.slow_pct)

        for eid in _to_remove:
            try:
                self.world.remove_component(eid, _Chan)
            except Exception:
                pass

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

    # ── Dano de magia server-side ────────────────────────────────────────────

    def _server_spell_damage(self, player_eid: int, dmg_weapon_pct: float, sp_coeff: float) -> int:
        """Delega para damage_calculator.spell_damage — fonte única compartilhada."""
        from damage_calculator import spell_damage as _sd
        return _sd(self.world, player_eid, dmg_weapon_pct, sp_coeff)

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

        target_cs.current_hp -= dmg  # sem clamp — overkill negativo preserva dano real

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
            _ai.state             = "AGGRO_DELAY"
            _ai.aggro_delay       = 0.5   # mesmo comportamento do range aggro, mas mais curto
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
        from skill_config import SKILL_CATALOG as _SC_bdf
        _bdf_data = _SC_bdf.get("bola_de_fogo", {})

        if target_id == -1:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return

        player_cs  = self.world.get_component(player_eid, CombatStats)
        char_stats = self.world.get_component(player_eid, CharacterStats)

        base_dmg = self._server_spell_damage(
            player_eid,
            _bdf_data.get("dmg_weapon_pct", 2.5),
            _bdf_data.get("dmg_sp_coeff",   2.0),
        )

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

        self._last_proj_spell_is_crit = is_crit
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
                # Notifica o cliente para aplicar o modificador visual e mostrar PROC
                self._last_proj_lapso_proc = {"bonus": _lapse, "duration": 5.0}

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
        from skill_config import SKILL_CATALOG as _SC_cal
        _cal = _SC_cal.get("calcinar", {})

        if target_id == -1:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return

        player_cs  = self.world.get_component(player_eid, CombatStats)
        char_stats = self.world.get_component(player_eid, CharacterStats)
        sp = player_cs.spell_power if player_cs else 0

        _base = _cal.get("base_dmg",    50)
        _coef = _cal.get("dmg_sp_coeff", 0.25)
        base_dmg = max(1, _base + int(sp * _coef))
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
        from components import CombatStats, TileMovement, Enemy
        from systems import apply_effect
        from utils import chebyshev
        from skill_config import SKILL_CATALOG as _SC_nc
        _nc = _SC_nc.get("nova_congelante", {})

        tm_p = self.world.get_component(player_eid, TileMovement)
        cs_p = self.world.get_component(player_eid, CombatStats)
        if not tm_p:
            return

        pl_x   = tm_p.current_tile_x
        pl_y   = tm_p.current_tile_y
        sp     = cs_p.spell_power if cs_p else 0
        _coef  = _nc.get("dmg_sp_coeff", 0.5)
        _range = _nc.get("cast_range", 3)

        for eid, _, etm, ecs in self.world.get_entities_with(
                Enemy, TileMovement, CombatStats):
            if ecs.current_hp <= 0:
                continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) > _range:
                continue
            dmg = max(1, int(sp * _coef))
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
