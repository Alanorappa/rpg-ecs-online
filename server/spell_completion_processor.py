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
import random


class SpellCompletionMixin:

    # Resultado de crit do último _server_apply_magic_damage com roll_crit=True.
    # Resetado antes de cada handler; lido pela coleta de resultados.
    _last_magic_is_crit: bool = False

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

            # Cobra concentração ao completar (arqueiro)
            _conc_cost = entry.get("concentration_cost", 0)
            if char and _conc_cost > 0:
                _conc_free = self.world.get_component(player_eid, _CS)
                if not (_conc_free and getattr(_conc_free, "concentration_free", False)):
                    char.concentration = max(0, char.concentration - _conc_cost)

            # Snapshot unificado: mobs + players PvP (padrão ECS — _combat_targets)
            hp_before, sfx_before = self._snapshot_combat_targets(exclude_eid=player_eid)

            # Spells com projétil: aguardam PROJECTILE_HIT_CS antes de aplicar dano
            _PROJECTILE_SPELLS = {"bola_de_fogo", "flecha_reiterada", "picada_escorpiao", "tiro_repulsivo"}
            _dispatch = {
                "nova_congelante":  self._server_nova_congelante,
                "polimorfia":       self._server_polimorfia,
                "calcinar":         self._server_calcinar,
                # ── Arqueiro ──────────────────────────────────────────────────
                "picada_escorpiao": self._server_picada_escorpiao,
                "flecha_reiterada": self._server_flecha_reiterada,
                "tiro_repulsivo":   self._server_tiro_repulsivo,
                "tiro_multiplo":    self._server_tiro_multiplo,
                "cancao_ninar":     self._server_cancao_ninar,
                "cancao_inspiracao":self._server_cancao_inspiracao,
                "so_um_gole":       self._server_so_um_gole,
                "camuflagem":       self._server_camuflagem,
                "recarregar":       self._server_recarregar,
            }
            from server.spell_debug_log import splog as _splog2
            if spell_id in _PROJECTILE_SPELLS:
                import time as _t_if
                # Flecha Reiterada: cada flecha manda PROJECTILE_HIT_CS individualmente.
                # Calcula quantas flechas esperar (inclui talento Sequência Final).
                _remaining = 1
                if spell_id == "flecha_reiterada":
                    from skill_config import SKILL_CATALOG as _SC_fr_cnt
                    _fr_cnt_p   = _SC_fr_cnt.get("flecha_reiterada", {}).get("params", {})
                    _remaining  = _fr_cnt_p.get("arrow_count", 2)
                    _att_fr_cnt = self.world.get_component(player_eid, _CS)
                    _tgt_fr_cnt = self.world.get_component(target_id,  _CS)
                    _thresh_fr  = getattr(_att_fr_cnt, "flecha_reiterada_hp_threshold", 0.0) \
                                  if _att_fr_cnt else 0.0
                    if (_tgt_fr_cnt and _thresh_fr > 0 and _tgt_fr_cnt.max_hp > 0
                            and (_tgt_fr_cnt.current_hp / _tgt_fr_cnt.max_hp) < _thresh_fr):
                        _remaining = 3
                self._spells_in_flight_queue.append({
                    "player_eid":     player_eid,
                    "spell_id":       spell_id,
                    "target_id":      target_id,
                    "entry":          entry,
                    "expires_at":     _t_if.time() + 3.0,
                    "remaining_hits": _remaining,
                })
                _splog2(f"COMPLETION {spell_id} player={player_eid} target={target_id} → em voo")
                # Notifica outros players via STATS_UPDATE com proj_incoming para que
                # criem o projétil visual. Inclui alvo para saber onde ele vai:
                # - PvP (target = player): proj_target = player_eid
                # - PvE (target = mob):   proj_target = mob_server_eid
                from components import CharacterStats as _CHS_proj
                for _other_eid in list(self._player_eids.values()):
                    if _other_eid == player_eid:
                        continue  # o caster já cria localmente
                    _vch_p = self.world.get_component(_other_eid, _CHS_proj)
                    _proj_xp_entry = {
                        "player_eid":    _other_eid,
                        "xp": 0, "mob_eid": -1,
                        "rage": _vch_p.rage if _vch_p else 0,
                        "proj_incoming": spell_id,
                        "proj_caster":   player_eid,
                        "proj_target":   target_id,
                    }
                    if spell_id == "flecha_reiterada":
                        _proj_xp_entry["proj_arrow_count"] = _remaining
                    self._pending_xp_deliveries.append(_proj_xp_entry)
            else:
                fn = _dispatch.get(spell_id)
                _tcs_pre = self.world.get_component(target_id, _CS)
                _splog2(f"COMPLETION {spell_id} player={player_eid} target={target_id} "
                        f"target_alive={_tcs_pre is not None and _tcs_pre.current_hp > 0} "
                        f"mobs_tracked={len(self._mob_eids)}")
                if fn:
                    self._last_magic_is_crit = False
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
                        "outcome":         "crit" if self._last_magic_is_crit else "hit",
                        "hp_after":        hp_after,
                        "applied_effects": applied,
                    }
                    if applied and _sfx2:
                        _res["effect_durations"] = {
                            ef: round(_sfx2.effects[ef].duration, 2)
                            for ef in applied if ef in _sfx2.effects
                        }
                    # Sincroniza slow_mult ao cliente para evitar desync visual do mob
                    if _sfx2:
                        _slow_eff = _sfx2.get("slow")
                        if _slow_eff:
                            _res["mob_slow_mult"] = _slow_eff.magnitude
                    results.append(_res)

            # PvP: HP + efeitos sync + rastreia dano para evitar FLT duplo
            for _r in results:
                _r_eid = _r["eid"]
                if _r_eid in self._player_eids.values():
                    from components import CombatStats as _CSvicC, CharacterStats as _CSvcC
                    _vcs = self.world.get_component(_r_eid, _CSvicC)
                    _vch = self.world.get_component(_r_eid, _CSvcC)
                    if _vcs:
                        _r_entry = {
                            "player_eid": _r_eid, "xp": 0, "mob_eid": -1,
                            "rage": _vch.rage if _vch else 0,
                            "hp": _r["hp_after"], "hp_max": _vcs.max_hp,
                        }
                        if _r.get("applied_effects"):
                            _r_entry["applied_effects"]  = _r["applied_effects"]
                            _r_entry["effect_durations"] = _r.get("effect_durations", {})
                        self._pending_xp_deliveries.append(_r_entry)
                    # Rastreia dano de spell para subtrair de mob_delta
                    if _r["damage"] > 0:
                        _pvd = getattr(self, "_pvp_damage_this_tick", {})
                        _pvd[_r_eid] = _pvd.get(_r_eid, 0) + _r["damage"]

            # SKILL_RESULT da conclusão do cast — toca som e aplica cooldown (GCD já foi).
            skill_entry: dict = {
                "caster_eid":   player_eid,
                "sid":          spell_id,
                "targets":      results,
                "cooldown":     None,
                "failed":       False,
                "is_completion": True,
                # Para spells com projétil: informa o alvo para o cliente criar o projétil
                "projectile_target": target_id if spell_id in _PROJECTILE_SPELLS else -1,
            }
            # Flecha Reiterada: envia n_arrows para cliente criar flechas corretas
            if spell_id == "flecha_reiterada":
                skill_entry["arrow_count"] = _remaining

            # Chama Interna: sincroniza proc ao cliente via SKILL_RESULT
            if char and getattr(char, "fire_instant_ready", False):
                skill_entry["fire_instant_proc"] = True

            self._skill_results_this_tick.append(skill_entry)

            # Sincroniza mana e concentração ao cliente após conclusão do cast
            if char:
                _compl_sync: dict = {
                    "player_eid": player_eid,
                    "xp":         0,
                    "mob_eid":    -1,
                    "rage":       char.rage,
                    "mana":       char.mana,
                }
                _conc_compl = getattr(char, "concentration", None)
                if _conc_compl is not None:
                    _compl_sync["concentration"] = _conc_compl
                self._pending_xp_deliveries.append(_compl_sync)

        # Libera is_casting (e portanto o auto-attack) para players cujo último
        # cast pendente acabou de resolver. Mantém True se ainda houver outro
        # cast enfileirado pelo mesmo player (ex: Flecha Reiterada com 2 casts).
        from components import CombatState as _CS_done
        _still_players = {e["player_eid"] for e in still}
        for _e_done in self._pending_spell_completions:
            _peid_done = _e_done["player_eid"]
            if _peid_done not in _still_players:
                _cst_done = self.world.get_component(_peid_done, _CS_done)
                if _cst_done:
                    _cst_done.is_casting = False

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
                if e.get("remaining_hits", 1) > 1:
                    # Multi-hit: mantém entrada para próximas flechas, só decrementa
                    import time as _t_rh
                    e["remaining_hits"] -= 1
                    e["expires_at"] = _t_rh.time() + 2.0  # renova timeout
                else:
                    del self._spells_in_flight_queue[i]
                break
        if entry is None:
            return  # expirou ou nunca foi enfileirado

        _dispatch = {
            "bola_de_fogo":     self._server_bola_de_fogo,
            "flecha_reiterada": self._server_flecha_reiterada,
            "picada_escorpiao": self._server_picada_escorpiao,
            "tiro_repulsivo":   self._server_tiro_repulsivo,
        }
        fn = _dispatch.get(spell_id)
        if not fn:
            return

        # Snapshot unificado: mobs + players PvP
        hp_before, sfx_before = self._snapshot_combat_targets(exclude_eid=player_eid)

        self._proj_spell_result = {"is_crit": False, "lapso_proc": None}
        try:
            fn(player_eid, target_id, entry["entry"])
        except Exception as _err:
            import traceback
            print(f"[ProjHit] ERRO {spell_id}: {_err}")
            traceback.print_exc()
            return

        _proj_is_crit  = self._proj_spell_result["is_crit"]
        _lapso_proc    = self._proj_spell_result["lapso_proc"]

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
                if applied and _sfx2:
                    _res2["effect_durations"] = {
                        ef: round(_sfx2.effects[ef].duration, 2)
                        for ef in applied if ef in _sfx2.effects
                    }
                if _sfx2:
                    _slow2 = _sfx2.get("slow")
                    if _slow2:
                        _res2["mob_slow_mult"] = _slow2.magnitude
                results.append(_res2)

        # PvP: HP + efeitos sync para vítimas players (BdF)
        for _r2 in results:
            _r2_eid = _r2["eid"]
            if _r2_eid in self._player_eids.values() and _r2["damage"] > 0:
                _vcs2 = self.world.get_component(_r2_eid, _CS)
                from components import CharacterStats as _CSv2
                _vch2 = self.world.get_component(_r2_eid, _CSv2)
                if _vcs2:
                    _r2_entry = {
                        "player_eid": _r2_eid, "xp": 0, "mob_eid": -1,
                        "rage": _vch2.rage if _vch2 else 0,
                        "hp": _r2["hp_after"], "hp_max": _vcs2.max_hp,
                    }
                    if _r2.get("applied_effects"):
                        _r2_entry["applied_effects"]  = _r2["applied_effects"]
                        _r2_entry["effect_durations"] = _r2.get("effect_durations", {})
                    self._pending_xp_deliveries.append(_r2_entry)
                # Rastreia dano BdF para subtrair de mob_delta
                _pvd2 = getattr(self, "_pvp_damage_this_tick", {})
                _pvd2[_r2_eid] = _pvd2.get(_r2_eid, 0) + _r2["damage"]

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

                for target_eid in list(self._combat_targets(exclude_eid=player_eid)):
                    mob_tm = self.world.get_component(target_eid, _TM)
                    mob_cs = self.world.get_component(target_eid, _CS)
                    if not mob_tm or not mob_cs or mob_cs.current_hp <= 0:
                        continue
                    if chebyshev(tx, ty, mob_tm.current_tile_x, mob_tm.current_tile_y) > ch.radius_tiles:
                        continue
                    dmg = max(1, int(cs_p.base_physical_damage * ch.dmg_weapon_pct
                                     + sp * ch.dmg_sp_coeff)) if cs_p else 1
                    self._server_apply_magic_damage(player_eid, target_eid, dmg,
                                                     roll_crit=True, report=True)
                    if ch.slow_pct > 0:
                        apply_effect(self.world, target_eid, "slow", 2.0, 1.0 - ch.slow_pct)

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

    # ── Ponto único de modificação de HP (servidor) ──────────────────────────

    def _apply_final_damage(self, target_id: int, dmg: int) -> bool:
        """Aplica dmg ao HP de target_id verificando todas as guardas do servidor.

        Retorna False se bloqueado (HP já zerado, is_immune, etc.).
        HP pode ficar negativo: overkill preservado para cálculo de dano real.

        ÚNICO lugar onde current_hp é decrementado por dano no servidor.
        Para adicionar redução de dano, resistências ou novos status de imunidade,
        editar apenas aqui.
        """
        from components import CombatStats as _CS, CombatState as _CSt
        cs = self.world.get_component(target_id, _CS)
        if not cs or cs.current_hp <= 0:
            return False
        cst = self.world.get_component(target_id, _CSt)
        if cst and cst.is_immune:
            return False
        cs.current_hp -= dmg
        return True

    # ── Dano de magia server-side ────────────────────────────────────────────

    def _server_spell_damage(self, player_eid: int, dmg_weapon_pct: float, sp_coeff: float) -> int:
        """Delega para damage_calculator.spell_damage — fonte única compartilhada."""
        from damage_calculator import spell_damage as _sd
        return _sd(self.world, player_eid, dmg_weapon_pct, sp_coeff)

    def _server_apply_magic_damage(self, attacker_id: int, target_id: int,
                                   dmg: int, is_crit: bool = False,
                                   roll_crit: bool = False, report: bool = False) -> bool:
        """Aplica dano mágico server-side (sem FLT/WARN/pygame).

        roll_crit=True: calcula crit internamente usando CombatStats do atacante.
        Retorna is_crit via self._last_magic_is_crit para coleta de resultados.

        report=True: emite COMBAT_RESULT (_combat_this_tick) + sync de HP PvP
        diretamente — usar APENAS quando o chamador não coleta resultados via
        snapshot hp_before/hp_after (ex.: _process_player_channeling, tick a
        tick). Handlers despachados por _process_spell_cast_completions /
        _apply_spell_on_projectile_hit (Calcinar, Nova Congelante, BdF) NÃO
        devem reportar aqui — o chamador já monta "results"/SKILL_RESULT a
        partir do snapshot, e reportar duas vezes duplicava o FLT de dano.
        """
        from components import (CombatStats, CombatState, AIControlled,
                                PendingDeath, StatusEffects, TileMovement, EntityIdentity)
        from stat_fns import enter_combat

        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return False

        if roll_crit:
            from damage_calculator import resolve_attack_outcome, CRITICAL_DAMAGE_MULTIPLIER
            attacker_cs = self.world.get_component(attacker_id, CombatStats)
            if attacker_cs:
                outcome, _ = resolve_attack_outcome(attacker_cs, target_cs, "magical")
                is_crit = (outcome == "crit")
                if is_crit:
                    dmg = int(dmg * CRITICAL_DAMAGE_MULTIPLIER)
        self._last_magic_is_crit = is_crit

        hp_before = target_cs.current_hp
        if not self._apply_final_damage(target_id, dmg):
            return False
        hp_after = max(0, target_cs.current_hp)
        damage   = max(0, hp_before - target_cs.current_hp)

        # Registra evento de combate — apenas quando report=True (chamador sem
        # coleta própria via snapshot, ex.: Calamidade Flamejante por tick).
        if report and damage > 0:
            self._combat_this_tick.append({
                "attacker": attacker_id,
                "target":   target_id,
                "damage":   damage,
                "outcome":  "crit" if is_crit else "hit",
                "hp_after": hp_after,
                "source":   "skill",
            })
            # PvP: HP sync para vítima player
            if target_id in self._player_eids.values():
                from components import CharacterStats as _CHS_mag
                _vch = self.world.get_component(target_id, _CHS_mag)
                self._pending_xp_deliveries.append({
                    "player_eid": target_id,
                    "xp": 0, "mob_eid": -1,
                    "rage": _vch.rage if _vch else 0,
                    "hp": hp_after, "hp_max": target_cs.max_hp,
                })

        # Quebra polimorfia
        _t_sfx = self.world.get_component(target_id, StatusEffects)
        if _t_sfx:
            _t_sfx.remove("polymorph")

        # Entra em combate — tanto atacante quanto vítima (PvP e mobs)
        attacker_state = self.world.get_component(attacker_id, CombatState)
        if attacker_state:
            enter_combat(attacker_state)
        target_state = self.world.get_component(target_id, CombatState)
        if target_state:
            enter_combat(target_state)

        _ai = self.world.get_component(target_id, AIControlled)
        if _ai and _ai.state in ("IDLE", "RETURNING"):
            _ai.state             = "AGGRO_DELAY"
            _ai.aggro_delay       = 0.5   # mesmo comportamento do range aggro, mas mais curto
            _ai.aggroed_by_damage = True
            _ai.path_recalc_timer = 0.0
            # Detector de aggro em world_server.py usa snapshot pré-tick e perde transições
            # ocorridas entre ticks/dentro de _process_spell_cast_completions e handlers
            # assíncronos de PROJECTILE_HIT_CS (aqui). Enfileira som diretamente para garantir
            # que o cliente ouça o aggro quando o dano mágico é aplicado, não ao pressionar a skill.
            _tm_aggr = self.world.get_component(target_id, TileMovement)
            _id_aggr = self.world.get_component(target_id, EntityIdentity)
            if _tm_aggr:
                self._pending_sound_events.append({
                    "kind":     "mob_aggro",
                    "mob_eid":  target_id,
                    "mob_name": _id_aggr.name if _id_aggr else "",
                    "tx":       _tm_aggr.current_tile_x,
                    "ty":       _tm_aggr.current_tile_y,
                })

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

        self._proj_spell_result["is_crit"] = is_crit
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
                self._proj_spell_result["lapso_proc"] = {"bonus": _lapse, "duration": 5.0}

        # Exaustão: slow progressivo por BdF consecutiva
        if player_cs and getattr(player_cs, "fire_exhaustion_enabled", False):
            from components import ActiveEffect as _AEX, StatusEffects as _SFX2
            _exh_dur = _bdf_data.get("effect_durations", {}).get("exhaustion", 6.0)
            _t_sfx = self.world.get_component(target_id, _SFX2)
            if _t_sfx is None:
                _t_sfx = _SFX2()
                self.world.add_component(target_id, _t_sfx)
            _exh = _t_sfx.get("exhaustion")
            if _exh:
                _new_stacks = min(_exh.magnitude + 1, 5)
                _exh.magnitude = _new_stacks
                _exh.duration  = _exh_dur
            else:
                _new_stacks = 1
                _t_sfx.effects["exhaustion"] = _AEX("exhaustion", _exh_dur, 1, 0.0)
            _slow_pct = (_new_stacks - 1) * 0.05
            if _slow_pct > 0:
                _slow = _t_sfx.get("slow")
                if _slow:
                    _slow.magnitude = min(_slow.magnitude, 1.0 - _slow_pct)
                    _slow.duration  = _exh_dur
                else:
                    _t_sfx.effects["slow"] = _AEX("slow", _exh_dur, 1.0 - _slow_pct, 0.0)

        # Chama Interna: proc após hit de spell de fogo
        if player_cs and char_stats:
            _proc_chance = getattr(player_cs, "fire_instant_proc_chance", 0.0)
            if _proc_chance > 0 and random.random() < _proc_chance:
                if not char_stats.fire_instant_ready:
                    char_stats.fire_instant_ready = True

    def _server_calcinar(self, player_eid: int, target_id: int, _entry: dict) -> None:
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

        self._server_apply_magic_damage(player_eid, target_id, base_dmg, roll_crit=True)

        # Chama Interna: proc após hit de fogo
        if player_cs and char_stats:
            _proc_chance = getattr(player_cs, "fire_instant_proc_chance", 0.0)
            if _proc_chance > 0 and random.random() < _proc_chance:
                if not char_stats.fire_instant_ready:
                    char_stats.fire_instant_ready = True

    def _server_nova_congelante(self, player_eid: int, target_id: int, entry: dict) -> None:
        from components import CombatStats, TileMovement
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

        from components import Position as _PosNC
        # Itera _combat_targets: mobs + players PvP (sem Enemy check, igual ao padrão ECS)
        for eid in self._combat_targets(exclude_eid=player_eid):
            etm = self.world.get_component(eid, TileMovement)
            ecs = self.world.get_component(eid, CombatStats)
            if not etm or not ecs or ecs.current_hp <= 0:
                continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) > _range:
                continue
            dmg = max(1, int(sp * _coef))
            self._server_apply_magic_damage(player_eid, eid, dmg, roll_crit=True)
            _root_dur = _nc.get("effect_durations", {}).get("root", 5.0)
            apply_effect(self.world, eid, "root", _root_dur)
            # Snapa mob para target_tile quando root é aplicado.
            # O cliente está animando em direção a target_tile — ao chegar lá, ambos
            # concordam com a mesma posição. Sem este snap, servidor fica em
            # current_tile (antes da animação completar) e cliente vai para target_tile,
            # causando desacordo que gera salto visual quando o root expira.
            if etm.is_moving:
                etm.current_tile_x = etm.target_tile_x
                etm.current_tile_y = etm.target_tile_y
                _nc_pos = self.world.get_component(eid, _PosNC)
                if _nc_pos:
                    from shared.constants import TILE_SIZE as _TS_nc
                    _nc_pos.x = etm.current_tile_x * _TS_nc + _TS_nc // 2
                    _nc_pos.y = etm.current_tile_y * _TS_nc + _TS_nc // 2
                etm.is_moving = False
                etm.progress  = 0.0

    def _server_polimorfia(self, player_eid: int, target_id: int, entry: dict) -> None:
        from components import CombatStats, CombatState
        from systems import apply_effect
        from skill_config import SKILL_CATALOG as _SC_poly

        if target_id == -1:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return

        _poly_dur = _SC_poly.get("polimorfia", {}).get("effect_durations", {}).get("polymorph", 6.0)
        regen_per_tick = max(1, int(target_cs.max_hp * 0.10))
        apply_effect(self.world, target_id, "polymorph", duration=_poly_dur, magnitude=regen_per_tick)

        attacker_state = self.world.get_component(player_eid, CombatState)
        if attacker_state:
            attacker_state.is_pursuing = False

    # ── Handlers — Arqueiro ───────────────────────────────────────────────────

    def _server_apply_ranged_physical(self, player_eid: int, target_id: int,
                                      ap_multiplier: float = 1.0,
                                      guaranteed_hit: bool = False,
                                      is_ability: bool = True) -> tuple:
        """Aplica dano físico ranged server-side.

        Retorna (is_dead, outcome, damage).
        """
        from components import CombatStats, CombatState, Equipment, PendingDeath
        from damage_calculator import (resolve_attack_outcome, calculate_base_damage,
                                       apply_armor_reduction, CRITICAL_DAMAGE_MULTIPLIER)
        from stat_fns import enter_combat
        from systems import apply_effect as _ae

        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return False, "miss", 0

        attacker_cs = self.world.get_component(player_eid, CombatStats)
        equip       = self.world.get_component(player_eid, __import__("components").Equipment)
        bow         = equip.slots.get("mainhand") if equip else None

        if guaranteed_hit:
            # Picada de Escorpião: sempre acerta, pode critar
            from damage_calculator import resolve_attack_outcome as _ro
            _dummy_cs = type("DC", (), {"crit_rating": getattr(attacker_cs,"crit_rating",0.05),
                                         "dodge_rating":0, "parry_rating":0, "block_rating":0,
                                         "block_value":0, "armor":0})()
            outcome, block_r = _ro(attacker_cs, _dummy_cs, "physical",
                                   is_ability=True) if attacker_cs else ("hit", 0.0)
            # Force: skip miss/dodge/parry — can only crit or hit
            if outcome not in ("crit", "hit", "block"):
                outcome = "hit"
        else:
            outcome, block_r = resolve_attack_outcome(attacker_cs, target_cs,
                                                       "physical", is_ability=is_ability) \
                if attacker_cs else ("hit", 0.0)

        if outcome in ("miss", "dodge", "parry"):
            return False, outcome, 0

        base = calculate_base_damage(attacker_cs, "physical", bow,
                                     multiplier=ap_multiplier,
                                     outcome=outcome, block_reduction=block_r) \
               if attacker_cs else 1.0
        dmg = max(1, int(apply_armor_reduction(base, attacker_cs, target_cs, outcome)))

        # Na Mosca: +25% no próximo disparo após crit
        if attacker_cs and getattr(attacker_cs, "na_mosca_bonus_active", False):
            dmg = int(dmg * 1.25)
            attacker_cs.na_mosca_bonus_active = False
        # Flechas Despadronizadas: 15% chance +50%
        if attacker_cs:
            _fdp = getattr(attacker_cs, "flechas_despadronizadas_chance", 0.0)
            if _fdp > 0 and random.random() < _fdp:
                dmg = int(dmg * 1.50)

        if not self._apply_final_damage(target_id, dmg):
            return False, "immune", 0

        # Na Mosca: ativa o bônus após crit
        if attacker_cs and getattr(attacker_cs, "na_mosca_enabled", False):
            if outcome == "crit":
                attacker_cs.na_mosca_bonus_active = True

        # Quebra polimorfia e entra em combate
        from components import StatusEffects as _SFX2, CombatState as _CS2, PendingDeath as _PD2
        _t_sfx = self.world.get_component(target_id, _SFX2)
        if _t_sfx:
            _t_sfx.remove("polymorph")
            # Sono quebra ao tomar dano — cancela also o slow encadeado
            _sleep_eff = _t_sfx.get("sleep")
            if _sleep_eff:
                _sleep_eff.on_expire_effect = ""
                _t_sfx.remove("sleep")
        attacker_cst = self.world.get_component(player_eid, _CS2)
        if attacker_cst:
            enter_combat(attacker_cst)
        target_cst = self.world.get_component(target_id, _CS2)
        if target_cst:
            enter_combat(target_cst)

        # Consume 1 flecha do carcás do servidor
        _eq = self.world.get_component(player_eid, __import__("components").Equipment)
        _qv = _eq.slots.get("offhand") if _eq else None
        if _qv and getattr(_qv, "item_type", "") == "quiver":
            _qv.arrow_count = max(0, _qv.arrow_count - 1)

        from components import AIControlled as _AIC2
        _ai = self.world.get_component(target_id, _AIC2)
        if _ai and _ai.state in ("IDLE", "RETURNING"):
            _ai.state              = "CHASING"
            _ai.aggroed_by_damage  = True
            _ai.target_eid         = player_eid
            _ai.path_recalc_timer  = 0.0
            _ai.target_lost_timer  = 0.0
            # Detector de aggro em world_server.py usa snapshot pré-tick e perde transições
            # ocorridas entre ticks (aqui). Enfileira som diretamente para garantir que o
            # cliente ouça o aggro quando a flecha acerta, não ao pressionar a skill.
            _tm_aggr = self.world.get_component(target_id, __import__("components").TileMovement)
            _id_aggr = self.world.get_component(target_id, __import__("components").EntityIdentity)
            if _tm_aggr:
                self._pending_sound_events.append({
                    "kind":     "mob_aggro",
                    "mob_eid":  target_id,
                    "mob_name": _id_aggr.name if _id_aggr else "",
                    "tx":       _tm_aggr.current_tile_x,
                    "ty":       _tm_aggr.current_tile_y,
                })

        if target_cs.current_hp <= 0:
            if not self.world.get_component(target_id, _PD2):
                self.world.add_component(target_id, _PD2(killer_entity_id=player_eid))
            return True, outcome, dmg
        return False, outcome, dmg

    def _server_picada_escorpiao(self, player_eid: int, target_id: int, entry: dict) -> None:
        from skill_config import SKILL_CATALOG as _SC
        from systems import apply_effect
        params = _SC.get("picada_escorpiao", {}).get("params", {})

        if target_id == -1:
            return
        ap_mult       = params.get("ap_multiplier",    1.5)
        slow_dur      = params.get("on_hit_duration",  3.0)
        slow_mag      = params.get("on_hit_magnitude", 0.30)

        dead, outcome, dmg = self._server_apply_ranged_physical(
            player_eid, target_id, ap_mult, guaranteed_hit=True)

        if outcome not in ("miss", "dodge", "parry") and dmg > 0:
            apply_effect(self.world, target_id, "slow", slow_dur, magnitude=slow_mag)

    def _server_flecha_reiterada(self, player_eid: int, target_id: int, entry: dict) -> None:
        """Aplica 1 flecha por chamada. Chamado uma vez por PROJECTILE_HIT_CS recebido."""
        from skill_config import SKILL_CATALOG as _SC
        params  = _SC.get("flecha_reiterada", {}).get("params", {})
        ap_mult = params.get("ap_multiplier", 2.0)

        if target_id == -1:
            return

        self._server_apply_ranged_physical(player_eid, target_id, ap_mult,
                                           guaranteed_hit=True)
        # Nota: _server_apply_ranged_physical já consome 1 flecha por chamada.

    def _server_tiro_repulsivo(self, player_eid: int, target_id: int, entry: dict) -> None:
        from skill_config import SKILL_CATALOG as _SC
        from components import (TileMovement, CombatStats, Position,
                                 CombatState, Tilemap)
        from shared.constants import TILE_SIZE as _TS
        from systems import apply_effect
        params       = _SC.get("tiro_repulsivo", {}).get("params", {})
        ap_mult      = params.get("ap_multiplier",  1.5)
        kb_tiles     = params.get("knockback_tiles", 5)
        stun_dur     = params.get("stun_duration",   3.0)

        if target_id == -1:
            return

        dead, outcome, dmg = self._server_apply_ranged_physical(
            player_eid, target_id, ap_mult, guaranteed_hit=True)
        if outcome in ("miss", "dodge", "parry", "immune"):
            return

        # Knockback: empurra nb tiles na direção oposta ao player
        p_tm  = self.world.get_component(player_eid, TileMovement)
        t_tm  = self.world.get_component(target_id,  TileMovement)
        if not p_tm or not t_tm:
            return

        dx = t_tm.current_tile_x - p_tm.current_tile_x
        dy = t_tm.current_tile_y - p_tm.current_tile_y
        dist = max(1, abs(dx) + abs(dy))
        sx = round(dx / dist)
        sy = round(dy / dist)
        if sx == 0 and sy == 0:
            sx = 1

        # Busca tilemap para verificar colisão
        tilemap_comp = None
        for _, tc in self.world.get_entities_with(Tilemap):
            tilemap_comp = tc
            break

        def _is_solid(tx, ty):
            if not tilemap_comp:
                return False
            rows = tilemap_comp.tile_matrix
            if 0 <= ty < len(rows) and 0 <= tx < len(rows[ty]):
                return rows[ty][tx].is_solid
            return True  # fora do mapa = sólido

        stunned = False
        for step in range(1, kb_tiles + 1):
            nx = t_tm.current_tile_x + sx
            ny = t_tm.current_tile_y + sy
            if _is_solid(nx, ny):
                stunned = True
                break
            t_tm.current_tile_x = nx
            t_tm.current_tile_y = ny
            t_tm.target_tile_x  = nx
            t_tm.target_tile_y  = ny
            t_pos = self.world.get_component(target_id, Position)
            if t_pos:
                t_pos.x = nx * _TS + _TS // 2
                t_pos.y = ny * _TS + _TS // 2
            self._moved_this_tick.append({
                "eid": target_id, "tx": nx, "ty": ny,
                "from_tx": nx - sx, "from_ty": ny - sy,
            })

        if stunned:
            apply_effect(self.world, target_id, "stun", stun_dur)

    def _server_tiro_multiplo(self, player_eid: int, target_id: int, entry: dict) -> None:
        from skill_config import SKILL_CATALOG as _SC
        from components import TileMovement, CombatStats
        import math
        params     = _SC.get("tiro_multiplo", {}).get("params", {})
        ap_mult    = params.get("ap_multiplier",   3.0)
        half_angle = params.get("cone_half_angle", 45.0)
        range_t    = params.get("range_tiles",     12)
        attacker_cs= self.world.get_component(player_eid, CombatStats)
        max_tgts   = getattr(attacker_cs, "tiro_multiplo_targets", 99) if attacker_cs else 99

        p_tm = self.world.get_component(player_eid, TileMovement)
        if not p_tm:
            return

        dir_x = entry.get("dir_x", 0.0)
        dir_y = entry.get("dir_y", 0.0)
        dlen  = math.hypot(dir_x, dir_y)
        if dlen < 0.001:
            return
        dir_x /= dlen
        dir_y /= dlen

        half_rad = math.radians(half_angle)
        cos_half = math.cos(half_rad)

        targets_hit = 0
        for eid in list(self._combat_targets(exclude_eid=player_eid)):
            if targets_hit >= max_tgts:
                break
            t_tm = self.world.get_component(eid, TileMovement)
            t_cs = self.world.get_component(eid, CombatStats)
            if not t_tm or not t_cs or t_cs.current_hp <= 0:
                continue
            dx = t_tm.current_tile_x - p_tm.current_tile_x
            dy = t_tm.current_tile_y - p_tm.current_tile_y
            dist = math.hypot(dx, dy)
            if dist > range_t or dist < 0.5:
                continue
            cos_angle = (dx * dir_x + dy * dir_y) / dist
            if cos_angle < cos_half:
                continue
            self._server_apply_ranged_physical(player_eid, eid, ap_mult,
                                               guaranteed_hit=True)
            targets_hit += 1

    def _server_cancao_ninar(self, player_eid: int, target_id: int, entry: dict) -> None:
        """Cast completo: o sono continua normalmente (já aplicado no início do
        canal por _skill_cancao_ninar). Apenas limpa lullaby_targets — o slow
        será aplicado via on_expire_effect quando o sono acabar."""
        from components import CharacterStats
        char_stats = self.world.get_component(player_eid, CharacterStats)
        if char_stats:
            char_stats.lullaby_targets.clear()

    def _server_cancao_inspiracao(self, player_eid: int, target_id: int, entry: dict) -> None:
        from skill_config import SKILL_CATALOG as _SC
        from stat_fns import add_timed_modifier
        from components import CombatStats, Modifier
        params   = _SC.get("cancao_inspiracao", {}).get("params", {})
        ap_pct   = params.get("ap_bonus_pct", 0.30)
        duration = params.get("duration",     20.0)
        cs = self.world.get_component(player_eid, CombatStats)
        if cs:
            mod = Modifier("attack_power", ap_pct, "percentage")
            add_timed_modifier(cs, mod, duration, label="cancao_inspiracao")

    def _server_so_um_gole(self, player_eid: int, target_id: int, entry: dict) -> None:
        from skill_config import SKILL_CATALOG as _SC
        from stat_fns import add_timed_modifier
        from components import CombatStats, Modifier
        params     = _SC.get("so_um_gole", {}).get("params", {})
        duration   = params.get("duration",    10.0)
        acerto_bns = params.get("acerto_flat", 100.0)
        cs = self.world.get_component(player_eid, CombatStats)
        if cs:
            cs.concentration_free       = True
            cs.concentration_free_timer = duration
            mod = Modifier("acerto", acerto_bns, "flat")
            add_timed_modifier(cs, mod, duration, label="so_um_gole")

    def _server_camuflagem(self, player_eid: int, target_id: int, entry: dict) -> None:
        from skill_config import SKILL_CATALOG as _SC
        from components import CombatStats, CombatState, TileMovement, AIControlled
        import random as _rand
        params    = _SC.get("camuflagem", {}).get("params", {})
        duration  = params.get("duration",  5.0)
        speed_pct = params.get("speed_pct", 0.30)

        cs  = self.world.get_component(player_eid, CombatStats)
        cst = self.world.get_component(player_eid, CombatState)
        tm  = self.world.get_component(player_eid, TileMovement)

        try:
            from tileset import CAMOUFLAGE_OBJECT_IDS
            chosen = _rand.choice(CAMOUFLAGE_OBJECT_IDS)
        except Exception:
            chosen = "tree"

        if cs:
            cs.camouflage_timer  = duration
            cs.camouflage_object = chosen
        if cst:
            cst.is_visible = False
        if tm:
            tm.speed = 110.0 * speed_pct

        # Mobs perdem o alvo
        for mob_eid in self._mob_eids:
            mob_ai  = self.world.get_component(mob_eid, AIControlled)
            mob_cst = self.world.get_component(mob_eid, CombatState)
            if mob_ai and mob_ai.target_eid == player_eid:
                mob_ai.target_eid        = -1
                mob_ai.aggroed_by_damage = False
                mob_ai.state             = "RETURNING"
            if mob_cst and mob_cst.target_entity_id == player_eid:
                mob_cst.target_entity_id = -1

    def _server_recarregar(self, player_eid: int, target_id: int, entry: dict) -> None:
        """Reabastece a aljava com flechas da mochila no servidor."""
        from components import Equipment, Inventory
        equip = self.world.get_component(player_eid, Equipment)
        inv   = self.world.get_component(player_eid, Inventory)
        if not equip or not inv:
            return
        quiver = equip.slots.get("offhand")
        if not quiver or getattr(quiver, "item_type", "") != "quiver":
            return
        if quiver.max_arrows == 0:
            quiver.max_arrows = 100

        for item in (inv.items if inv else []):
            if item is None:
                continue
            if item.item_type != "ammo" or item.stack <= 0:
                continue
            needed = quiver.max_arrows - quiver.arrow_count
            if needed <= 0:
                break
            take = min(needed, item.stack)
            item.stack       -= take
            quiver.arrow_count = min(quiver.max_arrows, quiver.arrow_count + take)
            quiver.subtype   = item.name
            if item.stack <= 0:
                inv.items[inv.items.index(item)] = None
            break
