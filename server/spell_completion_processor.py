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

    def _process_knockback_landings(self, dt: float) -> None:
        """Aplica stun + feedback de colisão só quando a tween de empurrão
        termina (mesma duração enviada ao cliente em _moved_this_tick),
        não no instante em que o servidor resolve o knockback (instantâneo).
        Sem isso o stun "começa" antes do alvo chegar visualmente ao ponto
        de colisão — servidor e cliente concordam: stun inicia quando o
        deslocamento acaba.
        """
        from engine.core_systems import apply_effect

        still = []
        for entry in self._pending_knockback_landings:
            entry["timer"] -= dt
            if entry["timer"] > 0:
                still.append(entry)
                continue

            target_id    = entry["target_id"]
            collided_eid = entry.get("collided_eid")
            if self.world.get_components_for_entity(target_id):
                apply_effect(self.world, target_id, "stun", entry["stun_dur"])
            if collided_eid is not None and self.world.get_components_for_entity(collided_eid):
                apply_effect(self.world, collided_eid, "stun", entry["stun_dur"])
            for _splash_eid in entry.get("splash_eids", ()):
                if self.world.get_components_for_entity(_splash_eid):
                    apply_effect(self.world, _splash_eid, "stun", entry["stun_dur"])

            self._skill_effects_this_tick.append({
                "sid": entry["sid"], "event": "collision",
                "caster_eid": entry["caster_eid"],
                "tx": entry["tx"], "ty": entry["ty"],
                "target_eid": target_id,
                "collided_eid": collided_eid if collided_eid is not None else -1,
            })
        self._pending_knockback_landings = still

    def _process_spell_cast_completions(self, dt: float) -> None:
        """Avança timers de spells pendentes e dispara efeitos quando concluídas."""
        from engine.components import CombatStats as _CS, CharacterStats as _CHS, StatusEffects as _SFX

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

            # Serviços globais no bundle do mapa DESTE caster antes dos handlers
            # (mesma razão de _process_skill_requests — ver register_map_services_for)
            self.register_map_services_for(player_eid)

            # Cobra mana ao completar (foi validada mas não deduzida no handler)
            char = self.world.get_component(player_eid, _CHS)
            if char and mana_cost > 0:
                char.mana = max(0, char.mana - mana_cost)
                # Skill Magic — só conta cast com custo efetivo de mana > 0 (exclui
                # Bloco de Gelo, que tem mana_cost no catálogo mas custo real 0).
                # Concedido por CAST, independente de acerto/dano — ver stats_system.py.
                from engine.components import SkillLevels as _SKLm
                skl = self.world.get_component(player_eid, _SKLm)
                cs_magic = self.world.get_component(player_eid, _CS)
                if skl and cs_magic:
                    from engine.stats_system import grant_skill_xp as _grant_magic_xp
                    _grant_magic_xp(skl, cs_magic, "magic", 1)

            # Evento de quest "use_skill" — toda magia/skill com tempo de
            # cast concluída conta, independente de acerto/dano. on_dummy
            # checa TrainingDummy no alvo (ex: "Iniciação Arcana"). Server-
            # autoritativo — ver quest_logic.py/PROBLEMAS_ARQUITETURA.md.
            from engine.components import TrainingDummy as _TDq
            from engine.quest_events import fire as _qfire_skill
            _qfire_skill("use_skill", player_eid=player_eid, skill_id=spell_id,
                         on_dummy=self.world.get_component(target_id, _TDq) is not None)

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
                "cancao_ninar":     self._server_cancao_ninar,
                "cancao_inspiracao":self._server_cancao_inspiracao,
                "so_um_gole":       self._server_so_um_gole,
                "camuflagem":       self._server_camuflagem,
                "recarregar":       self._server_recarregar,
            }
            from server.spell_debug_log import splog as _splog2
            _tiro_multiplo_targets: list = []
            _remaining = 1
            if spell_id == "tiro_multiplo":
                # Múltiplos alvos, não um target_id fixo: cada alvo selecionado no cone
                # recebe sua PRÓPRIA flecha (1 entrada em _spells_in_flight_queue cada),
                # resolvida individualmente por PROJECTILE_HIT_CS — igual ao projeto
                # offline (flecha física por alvo, não dano instantâneo de área). Ver
                # arquitetura/PROBLEMAS_ARQUITETURA.md.
                _tiro_multiplo_targets = self._complete_tiro_multiplo_cast(player_eid, entry)
                _splog2(f"COMPLETION tiro_multiplo player={player_eid} "
                        f"targets={_tiro_multiplo_targets} → em voo")
            elif spell_id in _PROJECTILE_SPELLS:
                import time as _t_if
                # Flecha Reiterada: cada flecha manda PROJECTILE_HIT_CS individualmente.
                # Calcula quantas flechas esperar (inclui talento Sequência Final).
                if spell_id == "flecha_reiterada":
                    from content.skill_config import SKILL_CATALOG as _SC_fr_cnt
                    _fr_cnt_p   = _SC_fr_cnt.get("flecha_reiterada", {}).get("params", {})
                    _remaining  = _fr_cnt_p.get("arrow_count", 2)
                    _att_fr_cnt = self.world.get_component(player_eid, _CS)
                    _tgt_fr_cnt = self.world.get_component(target_id,  _CS)
                    _thresh_fr  = getattr(_att_fr_cnt, "flecha_reiterada_hp_threshold", 0.0) \
                                  if _att_fr_cnt else 0.0
                    if (_tgt_fr_cnt and _thresh_fr > 0 and _tgt_fr_cnt.max_hp > 0
                            and (_tgt_fr_cnt.current_hp / _tgt_fr_cnt.max_hp) < _thresh_fr):
                        _remaining = 3
                # Tiro Repulsivo: congela a posição do atirador no momento do disparo
                # (= quando o projétil visual nasce no cliente). Sem isso, o knockback
                # usaria a posição ATUAL do atirador no momento do PROJECTILE_HIT_CS —
                # se ele andou durante o voo da flecha, a direção do empurrão diverge
                # da direção visual do tiro.
                if spell_id == "tiro_repulsivo":
                    from engine.components import TileMovement as _TM_tr_launch
                    _tm_launch = self.world.get_component(player_eid, _TM_tr_launch)
                    if _tm_launch:
                        entry["launch_tx"] = _tm_launch.current_tile_x
                        entry["launch_ty"] = _tm_launch.current_tile_y
                self._spells_in_flight_queue.append({
                    "player_eid":     player_eid,
                    "spell_id":       spell_id,
                    "target_id":      target_id,
                    "entry":          entry,
                    "expires_at":     _t_if.time() + 3.0,
                    "remaining_hits": _remaining,
                })
                _splog2(f"COMPLETION {spell_id} player={player_eid} target={target_id} → em voo")
                # Notificação de espectador (projétil cosmético) NÃO é mais enviada aqui —
                # vai junto do SKILL_EFFECT{event:"launch"} abaixo, que já é broadcast
                # AOI corretamente (raio de distância automático) para QUALQUER skill,
                # sem precisar de um canal/loop manual paralelo por feature. Antes havia
                # um loop aqui sobre TODOS os players conectados (sem filtro de AOI) +
                # uma allowlist de campos em session.py que cada skill nova precisava
                # lembrar de estender — causa raiz do bug "flecha não aparece pro
                # remoto" (Tiro Múltiplo) e da categoria inteira de bugs parecidos. Ver
                # arquitetura/PROBLEMAS_ARQUITETURA.md.
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
                    from engine.components import CombatStats as _CSvicC, CharacterStats as _CSvcC
                    _vcs = self.world.get_component(_r_eid, _CSvicC)
                    _vch = self.world.get_component(_r_eid, _CSvcC)
                    if _vcs:
                        _r_entry = {
                            "player_eid": _r_eid,
                            "rage": _vch.rage if _vch else 0,
                            "hp": _r["hp_after"], "hp_max": _vcs.max_hp,
                        }
                        if _r.get("applied_effects"):
                            _r_entry["applied_effects"]  = _r["applied_effects"]
                            _r_entry["effect_durations"] = _r.get("effect_durations", {})
                        self.queue_stats_update(_r_entry)
                    # Rastreia dano de spell para subtrair de mob_delta
                    if _r["damage"] > 0:
                        _pvd = getattr(self, "_pvp_damage_this_tick", {})
                        _pvd[_r_eid] = _pvd.get(_r_eid, 0) + _r["damage"]

            # SKILL_RESULT da conclusão do cast — toca som e aplica cooldown (GCD já foi).
            # cooldown: o CD EFETIVO registrado no cast (_skill_effective_cd, já com
            # reduções de talento) — antes era None e o cliente caía no CD BASE do
            # catálogo, divergindo da validação futura do servidor (mesma classe do
            # bug de rejeição falsa do Interceptar).
            skill_entry: dict = {
                "caster_eid":   player_eid,
                "sid":          spell_id,
                "targets":      results,
                "cooldown":     self._skill_effective_cd.get((player_eid, spell_id)),
                "failed":       False,
                "is_completion": True,
                # Para spells com projétil: informa o alvo para o cliente criar o projétil
                "projectile_target": target_id if spell_id in _PROJECTILE_SPELLS else -1,
            }
            # Flecha Reiterada: envia n_arrows para cliente criar flechas corretas
            if spell_id == "flecha_reiterada":
                skill_entry["arrow_count"] = _remaining
            # Tiro Múltiplo: lista de alvos (1 flecha cada) — não um único target_id
            if spell_id == "tiro_multiplo":
                skill_entry["projectile_targets"] = _tiro_multiplo_targets

            # Chama Interna: sincroniza proc ao cliente via SKILL_RESULT
            if char and getattr(char, "fire_instant_ready", False):
                skill_entry["fire_instant_proc"] = True

            self._skill_results_this_tick.append(skill_entry)

            # ── SKILL_EFFECT: evento de apresentação da conclusão ─────────────
            from engine.components import TileMovement as _TM_sfx
            _caster_tm_sfx = self.world.get_component(player_eid, _TM_sfx)
            _sfx_tx = _caster_tm_sfx.current_tile_x if _caster_tm_sfx else 0
            _sfx_ty = _caster_tm_sfx.current_tile_y if _caster_tm_sfx else 0
            if spell_id in _PROJECTILE_SPELLS or spell_id == "tiro_multiplo":
                # Projétil(eis) foram ao ar: launch event (impact chegará via PROJECTILE_HIT_CS).
                # Broadcast AOI automático (ver consume_skill_effects em session.py) — também
                # é o evento que o cliente usa para criar o projétil cosmético de espectador
                # (client/network_handlers.py::_handle_msg_skill_effect), sem precisar de
                # nenhum canal/loop manual paralelo por skill.
                _launch_evt = {
                    "sid": spell_id, "event": "launch",
                    "caster_eid": player_eid, "tx": _sfx_tx, "ty": _sfx_ty,
                    "target_eid": target_id,
                }
                if spell_id == "tiro_multiplo":
                    _launch_evt["target_eids"] = _tiro_multiplo_targets
                elif spell_id == "flecha_reiterada":
                    _launch_evt["arrow_count"] = _remaining
                self._skill_effects_this_tick.append(_launch_evt)
            else:
                # Spell resolve imediatamente: impact (ou miss se sem resultados)
                _sfx_event = "impact" if results else "miss"
                self._skill_effects_this_tick.append({
                    "sid": spell_id, "event": _sfx_event,
                    "caster_eid": player_eid, "tx": _sfx_tx, "ty": _sfx_ty,
                    "target_eid": target_id,
                })

            # Sincroniza mana e concentração ao cliente após conclusão do cast
            if char:
                _compl_sync: dict = {
                    "player_eid": player_eid,
                    "rage":       char.rage,
                    "mana":       char.mana,
                }
                _conc_compl = getattr(char, "concentration", None)
                if _conc_compl is not None:
                    _compl_sync["concentration"] = _conc_compl
                self.queue_stats_update(_compl_sync)

        # Libera is_casting (e portanto o auto-attack) para players cujo último
        # cast pendente acabou de resolver. Mantém True se ainda houver outro
        # cast enfileirado pelo mesmo player (ex: Flecha Reiterada com 2 casts).
        from engine.components import CombatState as _CS_done
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
        from engine.components import CombatStats as _CS, CharacterStats as _CHS, StatusEffects as _SFX

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
            "tiro_multiplo":    self._server_tiro_multiplo_hit,
        }
        fn = _dispatch.get(spell_id)
        if not fn:
            return

        # Snapshot unificado: mobs + players PvP
        hp_before, sfx_before = self._snapshot_combat_targets(exclude_eid=player_eid)

        # Serviços globais no bundle do mapa do caster (knockback do Tiro
        # Repulsivo valida colisão via tilemap — ver register_map_services_for)
        self.register_map_services_for(player_eid)

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
                from engine.components import CharacterStats as _CSv2
                _vch2 = self.world.get_component(_r2_eid, _CSv2)
                if _vcs2:
                    _r2_entry = {
                        "player_eid": _r2_eid,
                        "rage": _vch2.rage if _vch2 else 0,
                        "hp": _r2["hp_after"], "hp_max": _vcs2.max_hp,
                    }
                    if _r2.get("applied_effects"):
                        _r2_entry["applied_effects"]  = _r2["applied_effects"]
                        _r2_entry["effect_durations"] = _r2.get("effect_durations", {})
                    self.queue_stats_update(_r2_entry)
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

        # ── SKILL_EFFECT: impact ao acertar (ou miss se sem dano) ────────────
        from engine.components import TileMovement as _TM_ph
        _ph_tm = self.world.get_component(player_eid, _TM_ph)
        _ph_tx = _ph_tm.current_tile_x if _ph_tm else 0
        _ph_ty = _ph_tm.current_tile_y if _ph_tm else 0
        _ph_event = "impact" if results else "miss"
        self._skill_effects_this_tick.append({
            "sid": spell_id, "event": _ph_event,
            "caster_eid": player_eid, "tx": _ph_tx, "ty": _ph_ty,
            "target_eid": target_id,
        })

        # Sincroniza mana
        if char:
            self.queue_stats_update({
                "player_eid": player_eid,
                "rage":       char.rage,
                "mana":       char.mana,
            })

    # ── Channeling de players server-side (Calamidade Flamejante) ───────────

    def _process_player_channeling(self, dt: float) -> None:
        """Processa ticks de canalização de players (Calamidade Flamejante)."""
        from engine.components import Channeling as _Chan, CombatStats as _CS, \
                               CharacterStats as _CHS, TileMovement as _TM
        from engine.core_systems import apply_effect
        from engine.utils import chebyshev

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
                                                     roll_crit=True, report=True, school="fogo")
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
        from engine.components import IceBlockEffect as _IBE, CombatState as _CSt, CombatStats as _CS

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

    def _is_evading(self, target_id: int) -> bool:
        """True se target_id é um mob em modo evasão (AIControlled.state ==
        "RETURNING", voltando pro spawn após estourar o leash — ver
        EnemyAISystem/ARQUITETURA_ONLINE.md). Usado pelos handlers de skill
        mágica/à distância pra pular efeitos SECUNDÁRIOS (DoT, lentidão,
        knockback) num alvo evadindo — o dano direto já é bloqueado por
        apply_damage_core ("blocked_evade"), mas antes disso os handlers
        aplicavam esses efeitos incondicionalmente, mesmo o dano principal
        tendo sido barrado (mesmo buraco pré-existente que já afetava
        Bloco de Gelo/is_immune)."""
        from engine.components import AIControlled
        ai = self.world.get_component(target_id, AIControlled)
        return bool(ai and ai.state == "RETURNING")

    def _apply_final_damage(self, target_id: int, dmg: int, attacker_id: int = -1) -> bool:
        """Aplica dmg ao HP de target_id verificando todas as guardas.

        Retorna False se bloqueado (HP já zerado, is_immune, etc.).
        HP pode ficar negativo: overkill preservado para cálculo de dano real.

        Delegate de core_systems.apply_damage_core — núcleo COMPARTILHADO com
        deal_damage (melee, world_systems) e _apply_magic_damage (cliente
        offline, spell_system). Regra nova de mitigação/imunidade entra LÁ,
        uma vez, e vale para os 3 caminhos (problemas B/H resolvidos).
        add_pending_death=False: no servidor a morte é tratada pelos
        chamadores (snapshot hp_before/hp_after + death sweep), não aqui.

        attacker_id: alimenta o log de dano por mob (dono do loot/quest kill
        — ver WorldServer._log_mob_damage_hit/PROBLEMAS_ARQUITETURA.md).
        Chamadores (magia, ranged skill/auto-attack) sempre têm o atacante
        disponível — sem passar aqui, essas duas classes de dano nunca
        apareciam no log e o loot ia parar com quem desse a sorte de
        auto-atacar depois.
        """
        from engine.core_systems import apply_damage_core
        return apply_damage_core(self.world, target_id, dmg,
                                 killer_eid=attacker_id, add_pending_death=False,
                                 on_damage_dealt=self._log_mob_damage_hit
                                 ) in ("applied", "killed")

    # ── Dano de magia server-side ────────────────────────────────────────────

    def _server_spell_damage(self, player_eid: int, dmg_weapon_pct: float, sp_coeff: float) -> int:
        """Delega para damage_calculator.spell_damage — fonte única compartilhada."""
        from engine.damage_calculator import spell_damage as _sd
        return _sd(self.world, player_eid, dmg_weapon_pct, sp_coeff)

    def _server_apply_magic_damage(self, attacker_id: int, target_id: int,
                                   dmg: int, is_crit: bool = False,
                                   roll_crit: bool = False, report: bool = False,
                                   school: str = "") -> bool:
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

        school: "fogo"|"gelo"|"natureza" — aplica resist_<school> do alvo
        (skill level, ver stats_system.py) e concede 1 xp dessa resistência ao
        alvo. "" (default) = sem escola, sem resistência (ex: dano arcano).
        Bônus de Magic skill do atacante (dmg%+crit%) aplica sempre, com ou
        sem escola — qualquer spell que gasta mana conta para o skill Magic.
        """
        from engine.components import (CombatStats, CombatState, AIControlled,
                                PendingDeath, StatusEffects, TileMovement, EntityIdentity)
        from engine.stat_fns import enter_combat

        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return False

        # Modo evasão: bloqueia ANTES de rolar crit/resistência/xp — sem isso,
        # _is_evading() ainda bloquearia o dano final (apply_damage_core), mas
        # o alvo ganharia xp de resistência (grant_resist_skill_xp) por um
        # golpe que nunca vai acontecer.
        if self._is_evading(target_id):
            return False

        attacker_cs = self.world.get_component(attacker_id, CombatStats)
        _magic_crit_bonus = getattr(attacker_cs, "magic_skill_crit_bonus", 0.0) if attacker_cs else 0.0
        _magic_dmg_bonus  = getattr(attacker_cs, "magic_skill_dmg_bonus",  0.0) if attacker_cs else 0.0

        if roll_crit:
            from engine.damage_calculator import resolve_attack_outcome, CRITICAL_DAMAGE_MULTIPLIER
            if attacker_cs:
                outcome, _ = resolve_attack_outcome(attacker_cs, target_cs, "magical",
                                                    extra_crit=_magic_crit_bonus)
                is_crit = (outcome == "crit")
                if is_crit:
                    dmg = int(dmg * CRITICAL_DAMAGE_MULTIPLIER)
        self._last_magic_is_crit = is_crit

        if _magic_dmg_bonus > 0:
            dmg = int(dmg * (1.0 + _magic_dmg_bonus))

        if school in ("fogo", "gelo", "natureza"):
            from engine.damage_calculator import apply_resistance_reduction
            _resist = getattr(target_cs, f"resist_{school}", 0.0)
            dmg = max(1, int(apply_resistance_reduction(dmg, _resist)))
            from engine.stats_system import grant_resist_skill_xp
            grant_resist_skill_xp(self.world, target_id, school)

        hp_before = target_cs.current_hp
        if not self._apply_final_damage(target_id, dmg, attacker_id):
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
                from engine.components import CharacterStats as _CHS_mag
                _vch = self.world.get_component(target_id, _CHS_mag)
                self.queue_stats_update({
                    "player_eid": target_id,
                   
                    "rage": _vch.rage if _vch else 0,
                    "hp": hp_after, "hp_max": target_cs.max_hp,
                })

        # Entra em combate — tanto atacante quanto vítima (PvP e mobs)
        attacker_state = self.world.get_component(attacker_id, CombatState)
        if attacker_state:
            enter_combat(attacker_state)
        target_state = self.world.get_component(target_id, CombatState)
        if target_state:
            enter_combat(target_state)

        _ai = self.world.get_component(target_id, AIControlled)
        if _ai and _ai.state == "IDLE":
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
        from engine.components import CombatStats, StatusEffects, CharacterStats
        from engine.core_systems import apply_effect
        from engine.damage_calculator import CRITICAL_DAMAGE_MULTIPLIER, resolve_attack_outcome
        from content.skill_config import SKILL_CATALOG as _SC_bdf
        _bdf_data = _SC_bdf.get("bola_de_fogo", {})

        if target_id == -1:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return
        if self._is_evading(target_id):
            # Modo evasão: pula dano E efeitos secundários (burn/exaustão) —
            # sem isso, o dano já era bloqueado (apply_damage_core) mas o
            # burn/slow ainda vazavam pro alvo evadindo.
            return

        player_cs  = self.world.get_component(player_eid, CombatStats)
        char_stats = self.world.get_component(player_eid, CharacterStats)

        base_dmg = self._server_spell_damage(
            player_eid,
            _bdf_data.get("dmg_weapon_pct", 2.5),
            _bdf_data.get("dmg_sp_coeff",   2.0),
        )

        _magic_crit_bonus = getattr(player_cs, "magic_skill_crit_bonus", 0.0) if player_cs else 0.0
        outcome, _ = resolve_attack_outcome(player_cs, target_cs, "magical",
                                            extra_crit=_magic_crit_bonus)
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
        self._server_apply_magic_damage(player_eid, target_id, final_dmg, is_crit, school="fogo")

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
                from engine.components import Modifier
                from engine.stat_fns import add_timed_modifier
                add_timed_modifier(player_cs, Modifier("crit_rating", _lapse, "flat", source="buff"), 5.0, "lapso_elemental")
                # Notifica o cliente para aplicar o modificador visual e mostrar PROC
                self._proj_spell_result["lapso_proc"] = {"bonus": _lapse, "duration": 5.0}

        # Exaustão: slow progressivo por BdF consecutiva
        if player_cs and getattr(player_cs, "fire_exhaustion_enabled", False):
            from engine.components import ActiveEffect as _AEX, StatusEffects as _SFX2
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
        from engine.components import CombatStats, StatusEffects, CharacterStats
        from content.skill_config import SKILL_CATALOG as _SC_cal
        _cal = _SC_cal.get("calcinar", {})

        if target_id == -1:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return
        if self._is_evading(target_id):
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

        self._server_apply_magic_damage(player_eid, target_id, base_dmg, roll_crit=True, school="fogo")

        # Chama Interna: proc após hit de fogo
        if player_cs and char_stats:
            _proc_chance = getattr(player_cs, "fire_instant_proc_chance", 0.0)
            if _proc_chance > 0 and random.random() < _proc_chance:
                if not char_stats.fire_instant_ready:
                    char_stats.fire_instant_ready = True

    def _server_nova_congelante(self, player_eid: int, target_id: int, entry: dict) -> None:
        from engine.components import CombatStats, TileMovement
        from engine.core_systems import apply_effect
        from engine.utils import chebyshev
        from content.skill_config import SKILL_CATALOG as _SC_nc
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

        from engine.components import Position as _PosNC
        # Itera _combat_targets: mobs + players PvP (sem Enemy check, igual ao padrão ECS)
        for eid in self._combat_targets(exclude_eid=player_eid):
            etm = self.world.get_component(eid, TileMovement)
            ecs = self.world.get_component(eid, CombatStats)
            if not etm or not ecs or ecs.current_hp <= 0:
                continue
            if self._is_evading(eid):
                continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) > _range:
                continue
            dmg = max(1, int(sp * _coef))
            self._server_apply_magic_damage(player_eid, eid, dmg, roll_crit=True, school="gelo")
            _root_dur = _nc.get("effect_durations", {}).get("root", 5.0)
            apply_effect(self.world, eid, "root", _root_dur)
            # Snapa mob para target_tile quando root é aplicado.
            # O cliente está animando em direção a target_tile — ao chegar lá, ambos
            # concordam com a mesma posição. Sem este snap, servidor fica em
            # current_tile (antes da animação completar) e cliente vai para target_tile,
            # causando desacordo que gera salto visual quando o root expira.
            if etm.is_moving:
                from engine.utils import snap_to_tile as _snap_nc
                _snap_nc(self.world, eid, etm.target_tile_x, etm.target_tile_y)

    def _server_polimorfia(self, player_eid: int, target_id: int, entry: dict) -> None:
        from engine.components import CombatStats, CombatState, StatusEffects as _SFXpoly
        from engine.core_systems import apply_effect
        from content.skill_config import SKILL_CATALOG as _SC_poly
        from content.status_effects_data import EFFECT_DEFS as _EDEFS_poly

        if target_id == -1:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return

        # Dispela DoTs antes de aplicar polymorph — DoTs quebrariam a transformação
        # no próximo tick de dano. Remove efeitos periódicos de dano (tick_interval > 0,
        # is_buff=False) mas preserva CC (root, slow) e buffs.
        _sfx_poly = self.world.get_component(target_id, _SFXpoly)
        if _sfx_poly:
            _dot_keys = [k for k, v in _EDEFS_poly.items()
                         if not v.is_buff and v.tick_interval > 0 and k != "polymorph"]
            for _dk in _dot_keys:
                _sfx_poly.effects.pop(_dk, None)

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
        from engine.components import CombatStats, CombatState, Equipment, PendingDeath
        from engine.damage_calculator import (resolve_attack_outcome, calculate_base_damage,
                                       apply_armor_reduction, CRITICAL_DAMAGE_MULTIPLIER)
        from engine.stat_fns import enter_combat
        from engine.core_systems import apply_effect as _ae

        target_cs = self.world.get_component(target_id, CombatStats)
        if not target_cs or target_cs.current_hp <= 0:
            return False, "miss", 0
        if self._is_evading(target_id):
            # Bloqueia ANTES de rolar acerto/crit e conceder xp de arma/defesa
            # — sem isso, o alvo evadindo ainda ganharia essas duas xp por um
            # golpe que apply_damage_core ia barrar de qualquer forma.
            return False, "evade", 0

        # Linha de visão: NENHUM outro ponto do pipeline (auto-attack em
        # combat_processor.py, nem esta função) checava obstáculo entre
        # atirador e alvo — só existia uma checagem de LOS do lado do
        # CLIENTE (ui/spell_system.py::PlayerProjectileSystem), tarde
        # demais e só cosmética: o servidor já tinha aplicado dano,
        # descontado flecha e agrado o mob antes disso. Bug real reportado
        # pelo usuário (recorrente): atirar com parede na frente do alvo
        # tocava som + descontava flecha + agrava mob, mas sem dano nem
        # projétil (o client destruía o projétil silenciosamente ao
        # detectar o obstáculo, tarde demais pra desfazer o resto). Trata
        # como "miss" — mesmo outcome de um erro de verdade, então o
        # cliente já sabe renderizar (redireciona a flecha, sem consumir
        # aljava, sem agro) sem precisar de nenhuma mudança nova.
        from engine.components import TileMovement as _TM_los, Tilemap as _TMap_los
        _atk_tm = self.world.get_component(player_eid, _TM_los)
        _tgt_tm = self.world.get_component(target_id, _TM_los)
        if _atk_tm and _tgt_tm:
            _los_map = self.get_entity_map(target_id)
            _los_bundle = self._map_bundles.get(_los_map) if _los_map else None
            _los_tilemap = (self.world.get_component(_los_bundle.tilemap_entity, _TMap_los)
                            if _los_bundle is not None else None)
            if _los_tilemap is not None:
                from engine.world_systems import EnemyAISystem as _EAIS_los
                if not _EAIS_los._has_line_of_sight(
                        _los_tilemap, _atk_tm.current_tile_x, _atk_tm.current_tile_y,
                        _tgt_tm.current_tile_x, _tgt_tm.current_tile_y):
                    return False, "miss", 0

        attacker_cs = self.world.get_component(player_eid, CombatStats)
        equip       = self.world.get_component(player_eid, __import__("engine.components", fromlist=["Equipment"]).Equipment)
        bow         = equip.slots.get("mainhand") if equip else None

        # Skill level — Arco (atacante) afeta acerto+crit; Escudo/Defesa (alvo)
        # afetam block/avoid. Ver stats_system.py, seção Skill Level.
        from engine.stats_system import (weapon_skill_extras, defense_skill_extras,
                                  grant_weapon_skill_xp, grant_defense_skill_xp)
        _extra_acerto, _extra_crit_sk = weapon_skill_extras(self.world, player_eid, bow)
        _extra_block, _extra_avoid    = defense_skill_extras(self.world, target_id)
        grant_weapon_skill_xp(self.world, player_eid, bow)
        grant_defense_skill_xp(self.world, target_id)

        if guaranteed_hit:
            # Picada de Escorpião: sempre acerta, pode critar
            from engine.damage_calculator import resolve_attack_outcome as _ro
            _dummy_cs = type("DC", (), {"crit_rating": getattr(attacker_cs,"crit_rating",0.05),
                                         "dodge_rating":0, "parry_rating":0, "block_rating":0,
                                         "block_value":0, "armor":0})()
            outcome, block_r = _ro(attacker_cs, _dummy_cs, "physical",
                                   extra_crit=_extra_crit_sk,
                                   is_ability=True) if attacker_cs else ("hit", 0.0)
            # Force: skip miss/dodge/parry — can only crit or hit
            if outcome not in ("crit", "hit", "block"):
                outcome = "hit"
        else:
            outcome, block_r = resolve_attack_outcome(attacker_cs, target_cs,
                                                       "physical", extra_crit=_extra_crit_sk,
                                                       extra_acerto=_extra_acerto,
                                                       extra_block=_extra_block,
                                                       extra_avoid=_extra_avoid,
                                                       is_ability=is_ability) \
                if attacker_cs else ("hit", 0.0)

        # Consome 1 flecha do carcás assim que o tiro é autorizado e resolvido
        # (LOS/alcance/perseguição/munição já passaram em combat_processor.py,
        # e o alvo passou nos checks de morto/evadindo/LOS acima) — flecha foi
        # fisicamente disparada, então é gasta INDEPENDENTE do resultado
        # (hit/crit/block E TAMBÉM miss/dodge/parry). Correção do usuário
        # (10/07/2026): antes só consumia em hit/crit (depois de
        # _apply_final_damage), o que deixava miss "de graça" — errado, quem
        # atira e erra ainda gastou a flecha. Fica ANTES do
        # `if outcome in (miss/dodge/parry): return` de propósito.
        _eq_ar = self.world.get_component(player_eid, __import__("engine.components", fromlist=["Equipment"]).Equipment)
        _qv_ar = _eq_ar.slots.get("offhand") if _eq_ar else None
        if _qv_ar and getattr(_qv_ar, "item_type", "") == "quiver":
            _qv_ar.arrow_count = max(0, _qv_ar.arrow_count - 1)
            # Sincroniza a cópia LOCAL da aljava do caster — auto-attack já se
            # mantinha sincronizado via decremento espelhado no COMBAT_RESULT
            # (client/remote_entity_handlers.py:263), mas as 4 skills que
            # também consomem flecha por AQUI (Picada de Escorpião, Flecha
            # Reiterada, Tiro Repulsivo, Tiro Múltiplo — todas chamam esta
            # função) nunca avisavam o cliente. Resultado: `quiver.arrow_count`
            # local ficava sempre ACIMA do valor real do servidor a cada uso de
            # skill, então "Aljava vazia! Use Recarregar." (ui/systems.py) só
            # dispararia quando o cliente, defasado, também chegasse a 0 — até
            # lá o servidor já recusava o tiro silenciosamente (mesmo "continue"
            # sem feedback de LOS/alcance/cooldown) e o auto-attack simplesmente
            # não saía, sem nenhuma mensagem (bug real reportado pelo usuário
            # 13/07/2026: "tem 1 flecha na aljava mas ele não consegue atacar").
            # Mesmo padrão de confirmação já usado por Recarregar (linha ~1671).
            self.queue_stats_update({
                "player_eid":         player_eid,
                "quiver_arrow_count": _qv_ar.arrow_count,
            })

        if outcome in ("miss", "dodge", "parry"):
            return False, outcome, 0

        if is_ability and attacker_cs:
            # Skills de arco: fórmula única (arco + AP×(mult + 0.01×skill_level
            # do Arco)) — antes era (AP+arco)×mult, multiplicando a arma junto.
            # Crit/block aplicam via physical_fixed; armadura logo abaixo.
            from engine.damage_calculator import ability_physical_damage as _apd_rng
            _base_raw = _apd_rng(self.world, player_eid,
                                 {"damage_multiplier": ap_multiplier})
            base = calculate_base_damage(attacker_cs, "physical_fixed", bow,
                                         base_ability_damage=_base_raw,
                                         outcome=outcome, block_reduction=block_r)
        elif attacker_cs:
            # Auto-attack ranged: arco + AP×(1.0 + 0.01×skill_level do Arco) —
            # mesmo bônus de skill_level das skills físicas, agora também no
            # golpe básico (antes só ganhava +acerto/+crit via
            # weapon_skill_extras, nunca dano — inconsistência real entre
            # skill e auto-attack). Ver PROBLEMAS_ARQUITETURA.md.
            from engine.stats_system import weapon_skill_level as _wsl_ranged_auto
            _ap_skill_mult_auto = 1.0 + 0.01 * _wsl_ranged_auto(self.world, player_eid, bow)
            base = calculate_base_damage(attacker_cs, "physical", bow,
                                         multiplier=ap_multiplier,
                                         outcome=outcome, block_reduction=block_r,
                                         ap_skill_mult=_ap_skill_mult_auto)
        else:
            base = 1.0
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

        if not self._apply_final_damage(target_id, dmg, player_eid):
            return False, "immune", 0

        # Reciclagem: conta flechas acertadas neste alvo (auto-attack + skills
        # com flecha, ex.: Tiro Repulsivo, Picada de Escorpião) — payoff em
        # server_death_handler.py quando o alvo morre.
        target_cs.arrows_received += 1

        # Na Mosca: ativa o bônus após crit
        if attacker_cs and getattr(attacker_cs, "na_mosca_enabled", False):
            if outcome == "crit":
                attacker_cs.na_mosca_bonus_active = True

        # Quebra polimorfia e entra em combate
        from engine.components import StatusEffects as _SFX2, CombatState as _CS2, PendingDeath as _PD2
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

        from engine.components import AIControlled as _AIC2
        _ai = self.world.get_component(target_id, _AIC2)
        if _ai and _ai.state == "IDLE":
            _ai.state              = "CHASING"
            _ai.aggroed_by_damage  = True
            _ai.target_eid         = player_eid
            _ai.path_recalc_timer  = 0.0
            _ai.target_lost_timer  = 0.0
            # Detector de aggro em world_server.py usa snapshot pré-tick e perde transições
            # ocorridas entre ticks (aqui). Enfileira som diretamente para garantir que o
            # cliente ouça o aggro quando a flecha acerta, não ao pressionar a skill.
            _tm_aggr = self.world.get_component(target_id, __import__("engine.components", fromlist=["TileMovement"]).TileMovement)
            _id_aggr = self.world.get_component(target_id, __import__("engine.components", fromlist=["EntityIdentity"]).EntityIdentity)
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
        from content.skill_config import SKILL_CATALOG as _SC
        from engine.core_systems import apply_effect
        params = _SC.get("picada_escorpiao", {}).get("params", {})

        if target_id == -1:
            return
        ap_mult       = params.get("damage_multiplier", 1.5)
        slow_dur      = params.get("on_hit_duration",  3.0)
        slow_mag      = params.get("on_hit_magnitude", 0.30)

        dead, outcome, dmg = self._server_apply_ranged_physical(
            player_eid, target_id, ap_mult, guaranteed_hit=True)

        if outcome not in ("miss", "dodge", "parry") and dmg > 0:
            apply_effect(self.world, target_id, "slow", slow_dur, magnitude=slow_mag)

    def _server_flecha_reiterada(self, player_eid: int, target_id: int, entry: dict) -> None:
        """Aplica 1 flecha por chamada. Chamado uma vez por PROJECTILE_HIT_CS recebido."""
        from content.skill_config import SKILL_CATALOG as _SC
        params  = _SC.get("flecha_reiterada", {}).get("params", {})
        ap_mult = params.get("damage_multiplier", 2.0)

        if target_id == -1:
            return

        self._server_apply_ranged_physical(player_eid, target_id, ap_mult,
                                           guaranteed_hit=True)
        # Nota: _server_apply_ranged_physical já consome 1 flecha por chamada.

    def _server_tiro_repulsivo(self, player_eid: int, target_id: int, entry: dict) -> None:
        from content.skill_config import SKILL_CATALOG as _SC
        from engine.components import (TileMovement, CombatStats, Position,
                                 CombatState, Tilemap)
        from shared.constants import TILE_SIZE as _TS
        params       = _SC.get("tiro_repulsivo", {}).get("params", {})
        ap_mult      = params.get("damage_multiplier", 1.5)
        kb_tiles     = params.get("knockback_tiles", 5)
        stun_dur     = params.get("stun_duration",   3.0)

        if target_id == -1:
            return

        dead, outcome, dmg = self._server_apply_ranged_physical(
            player_eid, target_id, ap_mult, guaranteed_hit=True)
        if outcome in ("miss", "dodge", "parry", "immune", "evade"):
            # "evade": mob em modo evasão não pode ser empurrado nem stunado
            # por Tiro Repulsivo — dano já bloqueado, knockback também.
            return

        t_tm = self.world.get_component(target_id, TileMovement)
        if not t_tm:
            return

        # Direção: a partir da posição do atirador NO MOMENTO DO DISPARO (congelada
        # em entry["launch_tx/ty"], ver _process_spell_cast_completions) — usar a
        # posição ATUAL do atirador divergiria se ele andou durante o voo da flecha.
        launch_tx = entry.get("launch_tx")
        launch_ty = entry.get("launch_ty")
        if launch_tx is None or launch_ty is None:
            p_tm = self.world.get_component(player_eid, TileMovement)
            if not p_tm:
                return
            launch_tx, launch_ty = p_tm.current_tile_x, p_tm.current_tile_y

        # Bresenham (mesmo algoritmo de is_tile_walkable/_dash_path_clear), não
        # snap por sinal: sinal puro colapsa qualquer ângulo intermediário num
        # dos 8 eixos (ex.: dx=1,dy=3 tem ~72°, sinal virava 45°), divergindo
        # da direção real do tiro.
        dx = t_tm.current_tile_x - launch_tx
        dy = t_tm.current_tile_y - launch_ty
        from engine.utils import bresenham_ray
        kb_path = bresenham_ray(dx, dy, kb_tiles)

        # Tilemap do MAPA DO ALVO (multi-map: existem 3+ entidades Tilemap no
        # world — pegar "a primeira" validava colisão contra o mapa errado e
        # stunava em parede fantasma). Fallback: primeira, p/ mundo single-map.
        tilemap_comp = None
        _kb_map = self.get_entity_map(target_id)
        _kb_bundle = self._map_bundles.get(_kb_map) if _kb_map else None
        if _kb_bundle is not None:
            tilemap_comp = self.world.get_component(_kb_bundle.tilemap_entity, Tilemap)
        if tilemap_comp is None:
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

        # Índice de ocupação construído UMA VEZ por knockback — antes,
        # _entity_at_tile/_adjacent_creatures varriam TODOS os mobs+players
        # a cada chamada, e são chamadas por PASSO do empurrão (até ~15
        # varreduras O(N) por Tiro Repulsivo). tile → [eids] (lista: dois
        # corpos podem compartilhar tile durante animações). Snapshot é
        # seguro aqui: nada move DENTRO da resolução do knockback (o loop
        # abaixo só muda o current_tile do PRÓPRIO alvo, que nunca entra no
        # índice). Item (9)/B5 da auditoria — PROBLEMAS_ARQUITETURA.md §11.
        _occ_kb: dict = {}
        for other_eid in list(self._mob_eids) + list(self._player_eids.values()):
            if other_eid == target_id:
                continue
            o_tm = self.world.get_component(other_eid, TileMovement)
            if o_tm:
                _occ_kb.setdefault((o_tm.current_tile_x, o_tm.current_tile_y), []).append(other_eid)

        def _entity_at_tile(tx, ty, exclude_eid=None):
            """Outra criatura (mob ou player) ocupando o tile — exclui o próprio alvo."""
            for other_eid in _occ_kb.get((tx, ty), ()):
                if other_eid != exclude_eid:
                    return other_eid
            return None

        def _adjacent_creatures(tx, ty, exclude_eids):
            """Todas as criaturas a distância Chebyshev 1 de (tx, ty) — splash
            do stun no ponto de colisão, não só o eid que bloqueou o passo."""
            found = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue   # tile central excluído (mesma regra de antes)
                    for other_eid in _occ_kb.get((tx + dx, ty + dy), ()):
                        if other_eid not in exclude_eids:
                            found.append(other_eid)
            return found

        # Alvo é um player conectado (PvP): AOI_UPDATE.moved não chega até ele
        # mesmo (cliente ignora eid==self._my_eid no array de moved) — precisa
        # do canal de correção direta também.
        _target_is_player = target_id in self._player_eids.values()

        # Cancela qualquer movimento normal em andamento (ex: alvo estava no meio
        # de um passo de chase da IA quando a flecha acertou). O empurrão escreve
        # tile/Position diretamente e de forma instantânea (sem tween server-side)
        # — se is_moving continuasse True, TileMovementSystem ia recalcular
        # Position no próximo tick usando start_pixel/target_pixel ANTIGOS (do
        # passo de chase interrompido), sobrescrevendo a posição correta do
        # knockback e criando um "sprint"/correção visual no cliente.
        t_tm.is_moving = False

        # Referência à IA do alvo — usada abaixo (após calcular _duration) pra
        # represar a IA durante a janela de animação do dash. Buscado aqui
        # porque _server_apply_ranged_physical (chamado acima) já garante que
        # o alvo está CHASING/ATTACKING (aggro por dano), então o estado já
        # reflete o real antes do empurrão.
        from engine.components import AIControlled as _AICtrl_kb
        _ai_kb = self.world.get_component(target_id, _AICtrl_kb)

        # Posição de origem ANTES do empurrão — o cliente precisa dela pra
        # animar a tween inteira de uma vez (start→end), não passo a passo.
        _start_tx, _start_ty = t_tm.current_tile_x, t_tm.current_tile_y

        stunned        = False
        collided_eid   = None
        tiles_traveled = 0
        for step_x, step_y in kb_path:
            if step_x == 0 and step_y == 0:
                continue
            nx = t_tm.current_tile_x + step_x
            ny = t_tm.current_tile_y + step_y
            # Mesma regra de corte de canto de is_tile_walkable: passo diagonal
            # com os dois tiles ortogonais sólidos bloqueia mesmo se o tile
            # diagonal em si estiver livre (sprite não atravessa o vão).
            _corner_blocked = (step_x != 0 and step_y != 0
                                and _is_solid(t_tm.current_tile_x + step_x, t_tm.current_tile_y)
                                and _is_solid(t_tm.current_tile_x, t_tm.current_tile_y + step_y))
            if _is_solid(nx, ny) or _corner_blocked:
                stunned = True
                break
            # exclude_eid=player_eid: o atirador nunca pode ser o "bloqueador"
            # do próprio empurrão (viraria collided_eid e seria stunado pela
            # própria skill — mesma classe do bug do splash abaixo).
            _blocker = _entity_at_tile(nx, ny, exclude_eid=player_eid)
            # Passo diagonal: mesmo sem ocupar o tile exato do passo, uma
            # criatura num dos dois tiles "de canto" já teria o sprite
            # atravessado pelo alvo deslizando na diagonal — mesma regra de
            # corte de canto que is_tile_walkable aplica para paredes.
            if _blocker is None and step_x != 0 and step_y != 0:
                _blocker = (_entity_at_tile(t_tm.current_tile_x + step_x, t_tm.current_tile_y, exclude_eid=player_eid)
                            or _entity_at_tile(t_tm.current_tile_x, t_tm.current_tile_y + step_y, exclude_eid=player_eid))
            if _blocker is not None:
                stunned      = True
                collided_eid = _blocker
                break
            # Dentro do loop só avança current_tile (base dos checks de colisão
            # do próximo passo) — o snap completo (target_tile/pixels/Position/
            # is_moving) é feito UMA vez após o loop, via snap_to_tile.
            t_tm.current_tile_x = nx
            t_tm.current_tile_y = ny
            tiles_traveled += 1

        # Snap canônico na posição final (helper único de teleporte/knockback —
        # cobre também o reset de is_moving já feito acima, de forma idempotente).
        if tiles_traveled > 0:
            from engine.utils import snap_to_tile as _snap_kb
            _snap_kb(self.world, target_id, t_tm.current_tile_x, t_tm.current_tile_y)

        # Padrão de deslocamento forçado em rede (LoL/WoW e netcode de
        # referência): UM evento com posição final + duração total, não um
        # evento por tile. O cliente anima a tween inteira de uma vez em vez
        # de enfileirar N passos — elimina a fila descompassando da
        # perseguição real que já pode começar no mesmo tick, tile pulado por
        # dedup incorreto, e o atraso entre "servidor já terminou" e "cliente
        # ainda no meio da fila" que causava dano antes do mob chegar
        # visualmente. Servidor decide tudo; cliente só interpola o que foi
        # confirmado — nunca prediz o resultado de um empurrão em outra entidade.
        _DASH_TILE_S = 0.18  # mesma "sensação" de velocidade já usada antes, por tile
        _duration = tiles_traveled * _DASH_TILE_S

        # Represa a IA (estado AGGRO_DELAY = "fica parado", ver EnemyAISystem)
        # até a tween do empurrão terminar no cliente (_duration). Sem isso,
        # EnemyAISystem retomava o `path` antigo (calculado pra posição ANTES
        # do empurrão) já no próprio tick seguinte — `not tile_movement.is_moving`
        # (resetado acima) mais um path/recalc ainda válidos disparava
        # start_tile_movement imediatamente, e o mob "voltava" visualmente no
        # meio do dash que o cliente ainda estava animando (mesma classe do bug
        # de sprint do comentário acima, só que via IA, não TileMovementSystem).
        # AGGRO_DELAY expira sozinho e volta a CHASING com path_recalc_timer
        # novo — não precisa zerar path manualmente nem reimplementar o timer.
        #
        # Margem de segurança (_KB_AI_FREEZE_MARGIN): quando o empurrão colide
        # (stunned=True), _pending_knockback_landings usa essa MESMA _duration
        # pra decidir quando aplicar o stun (_process_knockback_landings). Os
        # dois timers (aggro_delay aqui, entry["timer"] lá) decrementam em
        # lockstep — sem margem, ambos cruzam zero NO MESMO TICK, mas
        # EnemyAISystem.update() roda ANTES de _process_knockback_landings
        # nesse tick (ver WorldServer._tick): a IA destrava e já pode mover 1
        # passo ANTES do stun efetivamente aterrissar — exatamente o "voltar
        # no dash" relatado, só que bem no instante do stun, não durante o
        # voo. A margem garante que o stun sempre aterrissa antes da IA
        # destravar (EnemyAISystem já trava movimento com stun ativo).
        _KB_AI_FREEZE_MARGIN = 0.1
        if _ai_kb:
            _ai_kb.state       = "AGGRO_DELAY"
            _ai_kb.aggro_delay = max(_ai_kb.aggro_delay, _duration + _KB_AI_FREEZE_MARGIN)
            _ai_kb.path        = None

        if tiles_traveled > 0:
            self._moved_this_tick.append({
                "eid": target_id, "tx": t_tm.current_tile_x, "ty": t_tm.current_tile_y,
                "from_tx": _start_tx, "from_ty": _start_ty,
                "is_dash": True, "duration": _duration,
            })
            if _target_is_player:
                self._skill_position_corrections.append({
                    "player_eid": target_id,
                    "tx": t_tm.current_tile_x, "ty": t_tm.current_tile_y,
                    "is_dash": True, "duration": _duration,
                })

        if stunned:
            # Splash: tudo adjacente (Chebyshev 1) ao ponto de colisão também
            # é pego pelo stun — não só o eid que literalmente bloqueou o
            # passo (ex.: 2 mobs agrupados, o segundo também deve travar).
            # player_eid (o PRÓPRIO atirador) SEMPRE excluído — bug real
            # reportado pelo usuário: alvo adjacente ao arqueiro colide a 0
            # tiles (empurrão nem sai do lugar), e como o arqueiro literalmente
            # está a distância Chebyshev 1 do ponto de colisão (ele mesmo), a
            # busca de splash pegava o próprio atirador e o stunava com a
            # própria flecha. Nunca faz sentido o autor do knockback ser
            # vítima dele (só em PvP, quando o CASTER é outro jogador, o alvo
            # member pode legitimamente ser splashado se estiver perto — aqui
            # é sempre o ATACANTE que fica de fora, nunca o alvo).
            _splash_eids = _adjacent_creatures(
                t_tm.current_tile_x, t_tm.current_tile_y,
                exclude_eids={target_id, collided_eid, player_eid})

            # Stun + feedback de colisão só pousam quando a tween de empurrão
            # termina (_duration), não agora — ver _process_knockback_landings.
            # Mesmo a 0 tiles (alvo já encostado), _duration=0 dispara no
            # próximo tick, mantendo o feedback sempre pós-deslocamento.
            self._pending_knockback_landings.append({
                "timer": _duration,
                "sid": "tiro_repulsivo",
                "target_id": target_id,
                "collided_eid": collided_eid,
                "splash_eids": _splash_eids,
                "stun_dur": stun_dur,
                "caster_eid": player_eid,
                "tx": t_tm.current_tile_x, "ty": t_tm.current_tile_y,
            })
        elif target_id in self._mob_eids:
            # Sem colisão (stun cobriria isso): o knockback resolve instantaneamente
            # no servidor, mas o cliente leva _duration segundos pra animar a tween
            # de saída — o mob já está livre pra perseguir/atacar no servidor antes
            # do cliente terminar de mostrar ele saindo. Usa a MESMA _duration do
            # broadcast acima (fonte única) — sem isso, dano podia "acontecer"
            # (autoritativo) com o mob ainda aparecendo longe na tela.
            from engine.components import CombatStats as _CS_kb
            _kb_cs = self.world.get_component(target_id, _CS_kb)
            if _kb_cs:
                _kb_cs.attack_cooldown_timer = max(_kb_cs.attack_cooldown_timer, _duration)

    def _complete_tiro_multiplo_cast(self, player_eid: int, entry: dict) -> list:
        """Ao concluir o canal: seleciona os alvos no cone de visão (mesmo cálculo
        de ângulo/range de antes) e enfileira 1 flecha por alvo em
        _spells_in_flight_queue — cada uma só aplica dano quando o cliente
        confirma a colisão via PROJECTILE_HIT_CS (_server_tiro_multiplo_hit),
        igual a Flecha Reiterada/Tiro Repulsivo. Antes, o dano de TODOS os
        alvos no cone era aplicado aqui mesmo, instantaneamente — sem flechas
        individuais, diferente da mecânica real (1 flecha por alvo, projeto
        offline) — ver arquitetura/PROBLEMAS_ARQUITETURA.md.

        Retorna a lista de target_ids selecionados (1 flecha cada)."""
        import time as _t_tm
        import math
        from content.skill_config import SKILL_CATALOG as _SC
        from engine.components import TileMovement, CombatStats, Equipment
        params     = _SC.get("tiro_multiplo", {}).get("params", {})
        half_angle = params.get("cone_half_angle", 45.0)
        range_t    = params.get("range_tiles",     12)
        attacker_cs= self.world.get_component(player_eid, CombatStats)
        max_tgts   = getattr(attacker_cs, "tiro_multiplo_targets", 0) if attacker_cs else 0

        p_tm = self.world.get_component(player_eid, TileMovement)
        if not p_tm or max_tgts <= 0:
            return []

        dir_x = entry.get("dir_x", 0.0)
        dir_y = entry.get("dir_y", 0.0)
        dlen  = math.hypot(dir_x, dir_y)
        if dlen < 0.001:
            return []
        dir_x /= dlen
        dir_y /= dlen

        half_rad = math.radians(half_angle)
        cos_half = math.cos(half_rad)

        candidates = []
        for eid in self._combat_targets(exclude_eid=player_eid):
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
            candidates.append((dist, eid))

        if not candidates:
            return []
        candidates.sort(key=lambda t: t[0])
        if max_tgts < 99:
            candidates = candidates[:max_tgts]

        # Limita pelas flechas disponíveis na aljava — cada acerto confirmado
        # consome 1 (_server_apply_ranged_physical, igual Flecha Reiterada).
        # Sem este cap, um cone com mais alvos do que flechas geraria acertos
        # "de graça" além da munição real.
        equip  = self.world.get_component(player_eid, Equipment)
        quiver = equip.slots.get("offhand") if equip else None
        arrows = getattr(quiver, "arrow_count", 0) if quiver else 0
        candidates = candidates[:arrows]

        targets = [eid for _, eid in candidates]
        for t_eid in targets:
            self._spells_in_flight_queue.append({
                "player_eid":     player_eid,
                "spell_id":       "tiro_multiplo",
                "target_id":      t_eid,
                "entry":          entry,
                "expires_at":     _t_tm.time() + 3.0,
                "remaining_hits": 1,
            })
        return targets

    def _server_tiro_multiplo_hit(self, player_eid: int, target_id: int, entry: dict) -> None:
        """1 flecha de Tiro Múltiplo acertando seu alvo — chamada uma vez por
        PROJECTILE_HIT_CS (1 por alvo selecionado em _complete_tiro_multiplo_cast)."""
        from content.skill_config import SKILL_CATALOG as _SC
        params  = _SC.get("tiro_multiplo", {}).get("params", {})
        ap_mult = params.get("damage_multiplier", 3.0)
        if target_id == -1:
            return
        self._server_apply_ranged_physical(player_eid, target_id, ap_mult,
                                           guaranteed_hit=True)

    def _server_cancao_ninar(self, player_eid: int, target_id: int, entry: dict) -> None:
        """Cast completo: o sono continua normalmente (já aplicado no início do
        canal por _skill_cancao_ninar). Apenas limpa lullaby_targets — o slow
        será aplicado via on_expire_effect quando o sono acabar."""
        from engine.components import CharacterStats
        char_stats = self.world.get_component(player_eid, CharacterStats)
        if char_stats:
            char_stats.lullaby_targets.clear()

    def _server_cancao_inspiracao(self, player_eid: int, target_id: int, entry: dict) -> None:
        from content.skill_config import SKILL_CATALOG as _SC
        from engine.stat_fns import add_timed_modifier
        from engine.components import CombatStats, Modifier
        params   = _SC.get("cancao_inspiracao", {}).get("params", {})
        ap_pct   = params.get("ap_bonus_pct", 0.30)
        duration = params.get("duration",     20.0)
        cs = self.world.get_component(player_eid, CombatStats)
        if cs:
            mod = Modifier("attack_power", ap_pct, "percentage", source="buff")
            add_timed_modifier(cs, mod, duration, label="cancao_inspiracao")

    def _server_so_um_gole(self, player_eid: int, target_id: int, entry: dict) -> None:
        from content.skill_config import SKILL_CATALOG as _SC
        from engine.stat_fns import add_timed_modifier
        from engine.components import CombatStats, Modifier
        params     = _SC.get("so_um_gole", {}).get("params", {})
        duration   = params.get("duration",    10.0)
        acerto_bns = params.get("acerto_flat", 100.0)
        cs = self.world.get_component(player_eid, CombatStats)
        if cs:
            cs.concentration_free       = True
            cs.concentration_free_timer = duration
            mod = Modifier("acerto", acerto_bns, "flat", source="buff")
            add_timed_modifier(cs, mod, duration, label="so_um_gole")

    def _server_camuflagem(self, player_eid: int, target_id: int, entry: dict) -> None:
        from content.skill_config import SKILL_CATALOG as _SC
        from engine.components import CombatStats, CombatState, TileMovement, AIControlled
        import random as _rand
        params    = _SC.get("camuflagem", {}).get("params", {})
        duration  = params.get("duration",  5.0)
        speed_pct = params.get("speed_pct", 0.60)

        cs  = self.world.get_component(player_eid, CombatStats)
        cst = self.world.get_component(player_eid, CombatState)
        tm  = self.world.get_component(player_eid, TileMovement)

        try:
            from engine.tileset import discover_camouflage_variants
            _variants = discover_camouflage_variants()
            chosen = _rand.choice(_variants) if _variants else ""
        except Exception:
            chosen = ""

        if cs:
            cs.camouflage_timer  = duration
            cs.camouflage_object = chosen
        if cst:
            cst.is_visible    = False
            cst.is_immune     = True
            cst.is_camouflaged = True
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
        """Reabastece a aljava com flechas da mochila no servidor.

        Envia confirmação ao cliente (arrow_count/subtype da aljava + munição
        consumida) — sem isso, a cópia LOCAL do cliente (arrow_count, item de
        munição na bag) nunca era atualizada: o servidor recarregava de
        verdade mas o cliente nunca ficava sabendo, divergindo pra sempre
        depois do primeiro uso online (bug real reportado por testers —
        "aljava diz estar cheia mas não está"). Ver PROBLEMAS_ARQUITETURA.md.
        """
        from engine.components import Equipment, Inventory
        equip = self.world.get_component(player_eid, Equipment)
        inv   = self.world.get_component(player_eid, Inventory)
        if not equip or not inv:
            return
        quiver = equip.slots.get("offhand")
        if not quiver or getattr(quiver, "item_type", "") != "quiver":
            return
        if quiver.max_arrows == 0:
            quiver.max_arrows = 100

        _ammo_name  = ""
        _ammo_taken = 0
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
            _ammo_name  = item.name
            _ammo_taken = take
            if item.stack <= 0:
                inv.items[inv.items.index(item)] = None
            break  # só o primeiro tipo de munição disponível por uso (comportamento original)

        self.queue_stats_update({
            "player_eid":         player_eid,
            "quiver_arrow_count": quiver.arrow_count,
            "quiver_max_arrows":  quiver.max_arrows,
            "quiver_subtype":     quiver.subtype,
            "ammo_name":          _ammo_name,
            "ammo_taken":         _ammo_taken,
        })
