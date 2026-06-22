"""
server/skill_processor.py
Mixin para WorldServer: processamento de skill requests por tick.
"""
from __future__ import annotations

from shared.constants import TILE_SIZE, TICK_RATE, LAG_COMP_WINDOW_MS

# Skills cujo handler compartilhado (skill_handlers.py) move o player e que têm
# uma predição visual correspondente no cliente (ver *_dash_visual em systems.py,
# chamada por _use_skill_visual_only). Se o handler FALHAR aqui sem mover o
# player (ex: Interceptar com "Caminho bloqueado"), a predição local já tocou a
# animação do dash sem saber disso — sem uma correção explícita, o cliente fica
# permanentemente desincronizado da posição real do servidor (todo check de
# range subsequente, como atacar, passa a falhar). Registrar nova skill aqui
# garante a mesma rede de segurança automaticamente.
_MOVEMENT_PREDICTED_SKILLS = {"interceptar"}


class SkillProcessorMixin:

    def _process_skill_requests(self) -> None:
        """Processa todas as skills enfileiradas para este tick."""
        from components import CombatState, CombatStats, TileMovement, GhostState
        from skill_config import SKILL_CATALOG
        from components import PlayerSkills as _PS
        requests = list(self._pending_skill_requests)
        self._pending_skill_requests.clear()

        for req in requests:
            player_eid = req["player_eid"]
            sid        = req["sid"]

            # Player morto (corpo) ou espírito (ghost): nunca executa skills —
            # mesmo que o cliente esteja com bug visual ou tente burlar can_act().
            _gst_skp = self.world.get_component(player_eid, GhostState)
            if _gst_skp is not None and _gst_skp.is_dead:
                continue

            # Stun/cast/disoriented/polymorph/sleep: can_act()==False — nunca executa
            # skills, mesmo que o cliente tente burlar (igual ao bypass de GhostState
            # acima). Espelha o bloqueio client-side de PlayerInputSystem (systems.py).
            _cs_skp = self.world.get_component(player_eid, CombatState)
            if _cs_skp is not None and not _cs_skp.can_act():
                continue
            from utils import is_action_locked as _is_action_locked_skp
            if _is_action_locked_skp(self.world, player_eid):
                continue

            # Constrói objeto Skill a partir do SKILL_CATALOG (servidor não tem PlayerSkills)
            # Tenta primeiro no PlayerSkills local se existir (ex: skills com estado de cargas)
            skill_obj = None
            player_skills = self.world.get_component(player_eid, _PS)
            if player_skills:
                for sk in player_skills.skills:
                    if sk and sk.skill_id == sid:
                        skill_obj = sk
                        break
            if skill_obj is None:
                # Fallback: cria instância temporária do SKILL_CATALOG
                skill_obj = _PS._make_skill(sid, SKILL_CATALOG) if sid in SKILL_CATALOG else None
            if skill_obj is None:
                print(f"[Skill] sid='{sid}' não encontrado no SKILL_CATALOG")
                continue

            # ── Validação server-side de cooldown ─────────────────────────────
            # Impede spam mesmo que o cliente manipule current_cooldown local.
            # Skills com cooldown=0 (Golpe Poderoso, Executar) passam sempre;
            # skills com cooldown>0 são bloqueadas se o tempo decorrido for menor.
            import time as _time_mod
            _sk_key  = (player_eid, sid)
            _now_srv = _time_mod.time()
            _elapsed = _now_srv - self._skill_last_used.get(_sk_key, 0.0)
            # Usa o CD efetivo do uso anterior (com reduções de talento), não o base.
            # Sem isso, talentos que reduzem CD do Interceptar causam rejeição falsa:
            # o cliente conta 15s (CD reduzido) mas o servidor valida contra 22s (base).
            _sk_cd   = self._skill_effective_cd.get(_sk_key,
                           getattr(skill_obj, "cooldown", 0.0))
            if _sk_cd > 0 and _elapsed < _sk_cd:
                _cd_remaining = _sk_cd - _elapsed
                print(f"[Skill] REJEITADO (CD) {sid} player={player_eid} "
                      f"restante={_cd_remaining:.1f}s")
                # Notifica cliente: limpa _server_pending e sincroniza CD local
                self._skill_results_this_tick.append({
                    "caster_eid": player_eid,
                    "sid":        sid,
                    "targets":    [],
                    "cooldown":   _cd_remaining,
                    "failed":     True,
                })
                continue
            # NÃO registra CD aqui — só registra APÓS handler retornar True.
            # Registrar antes causaria 15s de CD desperdiçado em falhas legítimas
            # (fora de range, "Sem cargas") e silenciaria o próximo uso válido.

            # Aponta o SkillSystem para este player
            self._skill_system.player_entity_id = player_eid

            # Snapshot unificado: mobs + players PvP (padrão _combat_targets)
            hp_snapshot, effects_snapshot = self._snapshot_combat_targets(exclude_eid=player_eid)

            # Obtém componentes necessários para os handlers
            combat_stats = self.world.get_component(player_eid, CombatStats)
            combat_state = self.world.get_component(player_eid, CombatState)
            tile_move    = self.world.get_component(player_eid, TileMovement)
            if not combat_stats or not tile_move:
                continue

            # Snapshot do HP do próprio player (para detectar auto-cura/dano próprio)
            _player_hp_before = combat_stats.current_hp

            # Snapshot de procs e mana antes do handler
            from components import CharacterStats as _CSsnap
            _char_snap        = self.world.get_component(player_eid, _CSsnap)
            _free_exec_before = getattr(_char_snap, "free_executar_charges", 0) if _char_snap else 0
            _mana_before_handler = getattr(_char_snap, "mana", 0) if _char_snap else 0

            # Seta target no servidor usando tid do CAST_SKILL (server_eid enviado pelo cliente)
            # tid pode ser mob (PvE) ou player (PvP — exceto o próprio caster)
            tid = req.get("tid", -1)
            if tid != -1 and combat_state:
                if tid in self._mob_eids:
                    combat_state.target_entity_id = tid
                elif tid in self._player_eids.values() and tid != player_eid:
                    combat_state.target_entity_id = tid  # alvo PvP

            from server.spell_debug_log import splog as _splog
            _splog(f"CAST_SKILL sid={sid} player={player_eid} tid={tid} "
                   f"tid_in_mobs={tid in self._mob_eids} "
                   f"cs.target={getattr(combat_state,'target_entity_id',-1)}")

            # Lag compensation: snapa posições para o range check de skill.
            # Player: sempre usa target_tile (cliente vê a si mesmo no destino).
            # Mob: SÓ snapa se progress >= 0.5 — espelha a predição do cliente
            #   (sistemas.py _process_target: quando mob >= 50% do caminho, usa target_tile).
            #   Para kiting (mob acabou de sair), progress < 0.5 → usa current_tile,
            #   evitando que a lag comp aumente artificialmente a distância.
            # Lag compensation: snapa tile E Position para o centro do tile alvo.
            # Position.x/y é interpolado (mid-animation) — sem este snap, o range check
            # em pixels usaria posição errada mesmo com tile correto.
            _mob_tile_snapshots: dict[int, tuple] = {}    # eid → (tile_x, tile_y, pos_x, pos_y)
            _player_tile_snap   = None                    # (tile_x, tile_y, pos_x, pos_y)
            from components import Position as _PosSnap, TileMovement as _TM
            if tid != -1 and tid in self._mob_eids:
                _mob_tm  = self.world.get_component(tid, _TM)
                _mob_pos = self.world.get_component(tid, _PosSnap)
                if _mob_tm:
                    _old = (_mob_tm.current_tile_x, _mob_tm.current_tile_y,
                            _mob_pos.x if _mob_pos else 0,
                            _mob_pos.y if _mob_pos else 0)
                    _mob_tile_snapshots[tid] = _old
                    if _mob_tm.is_moving:
                        # Determina posição de referência para o range check.
                        # Player snapped para target_tile — usa o mesmo como base.
                        _p_ref_x = tile_move.target_tile_x if tile_move.is_moving else tile_move.current_tile_x
                        _p_ref_y = tile_move.target_tile_y if tile_move.is_moving else tile_move.current_tile_y
                        from utils import chebyshev as _cheb_snap
                        _d_cur = _cheb_snap(_p_ref_x, _p_ref_y,
                                            _mob_tm.current_tile_x, _mob_tm.current_tile_y)
                        _d_tgt = _cheb_snap(_p_ref_x, _p_ref_y,
                                            _mob_tm.target_tile_x,  _mob_tm.target_tile_y)
                        if _mob_tm.progress >= 0.5 and _d_tgt <= _d_cur:
                            # Mob se aproximando: snapa para target_tile (espelha predição cliente)
                            _mob_tm.current_tile_x = _mob_tm.target_tile_x
                            _mob_tm.current_tile_y = _mob_tm.target_tile_y
                            if _mob_pos:
                                _mob_pos.x = _mob_tm.target_tile_x * TILE_SIZE + TILE_SIZE / 2
                                _mob_pos.y = _mob_tm.target_tile_y * TILE_SIZE + TILE_SIZE / 2
                        elif _mob_pos:
                            # Mob se afastando (kiting) ou progress < 0.5: usa centro do
                            # current_tile — remove bias de interpolação que causa falso
                            # "fora de alcance" em diagonal (ex: goblins hunters).
                            _mob_pos.x = _mob_tm.current_tile_x * TILE_SIZE + TILE_SIZE / 2
                            _mob_pos.y = _mob_tm.current_tile_y * TILE_SIZE + TILE_SIZE / 2
            # Player: snapa sempre para target_tile (cliente usa prediction)
            _player_pos = self.world.get_component(player_eid, _PosSnap)
            if tile_move.is_moving:
                _player_tile_snap = (tile_move.current_tile_x, tile_move.current_tile_y,
                                     _player_pos.x if _player_pos else 0,
                                     _player_pos.y if _player_pos else 0)
                tile_move.current_tile_x = tile_move.target_tile_x
                tile_move.current_tile_y = tile_move.target_tile_y
                if _player_pos:
                    _player_pos.x = tile_move.target_tile_x * TILE_SIZE + TILE_SIZE / 2
                    _player_pos.y = tile_move.target_tile_y * TILE_SIZE + TILE_SIZE / 2

            # Para skills direcionais (Pirofagia, Tiro Múltiplo), injeta direção
            tile_move._server_dir_x = req.get("dir_x", 0.0)
            tile_move._server_dir_y = req.get("dir_y", 0.0)
            # Para skills AOE com clique (Calamidade Flamejante): dir_x/y carregam coordenadas world
            tile_move._server_aoe_x = req.get("dir_x", 0.0)
            tile_move._server_aoe_y = req.get("dir_y", 0.0)

            # Lag compensation por timestamp para skills de cone (dir != 0, sem alvo fixo).
            # Usa snapshot histórico: posições dos mobs quando o cliente disparou,
            # em vez das posições atuais que chegaram ~latência ms depois.
            # Janela máxima: LAG_COMP_WINDOW_MS (200ms) = SNAPSHOT_HISTORY/TICK_RATE.
            _lag_restored: dict[int, tuple[int, int]] = {}
            _is_cone = (tile_move._server_dir_x != 0.0 or tile_move._server_dir_y != 0.0) and tid == -1
            if _is_cone:
                _client_ts_ms = req.get("ts", 0)
                if _client_ts_ms:
                    _lag_ms = _now_srv * 1000.0 - _client_ts_ms
                    _lag_ms = max(0.0, min(float(_lag_ms), float(LAG_COMP_WINDOW_MS)))
                    _ticks_ago = int(_lag_ms / (1000.0 / TICK_RATE))
                    _hist_snap = self.get_snapshot_at(self.tick_count - _ticks_ago)
                    from components import TileMovement as _TM_lc
                    for _lc_eid, (_lc_tx, _lc_ty) in _hist_snap.items():
                        _lc_tm = self.world.get_component(_lc_eid, _TM_lc)
                        if _lc_tm:
                            _lag_restored[_lc_eid] = (_lc_tm.current_tile_x, _lc_tm.current_tile_y)
                            _lc_tm.current_tile_x = _lc_tx
                            _lc_tm.current_tile_y = _lc_ty

            # Skills ofensivas: enter_combat + is_pursuing (copiado de _use_skill:5299-5305)
            # offensive=True + cast_time==0 → enter_combat + is_pursuing=True
            # offensive=True + cast_time>0  → só enter_combat (evita aggro prematuro)
            _is_offensive = getattr(skill_obj, "offensive", True)
            _has_cast     = getattr(skill_obj, "cast_time", 0.0) > 0
            if _is_offensive and combat_state:
                from stat_fns import enter_combat as _ec2
                _ec2(combat_state)
                if not _has_cast:
                    combat_state.is_pursuing = True

            # Snapshot de posição do player antes do handler (para detectar dash/teleporte)
            _tx_before = tile_move.target_tile_x
            _ty_before = tile_move.target_tile_y

            # Expõe lista de spells pendentes ao handler (detecta modo servidor)
            self._skill_system._server_pending_spells = self._pending_spell_completions
            _pending_count_before = len(self._pending_spell_completions)
            # Expõe lista de mudanças de visibilidade (ex: Camuflagem) — handler
            # registra aqui em vez de direto em world_server (self é o SkillSystem,
            # não o WorldServer, ver _server_visibility_changed em skill_handlers.py)
            self._skill_system._server_visibility_changed = self._visibility_changed_this_tick

            # Reseta last_outcome antes do handler: para skills com cast_time que
            # só ENFILEIRAM a conclusão (sem deal_damage agora), last_outcome
            # ficava com o valor de um ataque anterior (ex: "miss" do auto-attack)
            # e o bloco abaixo ("elif _skill_outcome in miss/dodge/...") gerava um
            # SKILL_RESULT falso de "Errou!" no cast_started, antes da skill resolver.
            import systems as _sys_reset
            _combat_svc_reset = getattr(_sys_reset, "_svc", {}).get("combat")
            if _combat_svc_reset:
                _combat_svc_reset.last_outcome = "hit"

            # Chama o handler diretamente (mesmo mecanismo do SkillSystem offline)
            handler_fn = getattr(self._skill_system, f"_skill_{sid}", None)
            if handler_fn:
                try:
                    _skill_ok = handler_fn(skill_obj, combat_stats, combat_state, tile_move)
                    _splog(f"  handler _skill_{sid} -> ok={_skill_ok} "
                           f"pending_spells={len(self._pending_spell_completions)}")
                    # Skill com cast_time enfileirou conclusão diferida → bloqueia
                    # can_act() (auto-attack) até o cast resolver, espelhando o
                    # offline (combat_state.is_casting = True em systems.py:5877).
                    # Sem isso, o auto-attack genérico dispara (e agra o mob) durante
                    # o cast da própria skill, "antes" do dano dela ser confirmado.
                    if (_skill_ok and _has_cast and combat_state
                            and len(self._pending_spell_completions) > _pending_count_before):
                        combat_state.is_casting = True
                    # Restaura posições de lag comp de cone skills (timestamp-based)
                    for _lc_eid, (_orig_tx, _orig_ty) in _lag_restored.items():
                        _lc_tm2 = self.world.get_component(_lc_eid, _TM)
                        if _lc_tm2:
                            _lc_tm2.current_tile_x = _orig_tx
                            _lc_tm2.current_tile_y = _orig_ty
                    # Restaura current_tile E Position do mob e player (inline snap)
                    for _mob_eid, (_old_cx, _old_cy, _old_px, _old_py) in _mob_tile_snapshots.items():
                        _m_tm  = self.world.get_component(_mob_eid, _TM)
                        _m_pos = self.world.get_component(_mob_eid, _PosSnap)
                        if _m_tm:
                            _m_tm.current_tile_x = _old_cx
                            _m_tm.current_tile_y = _old_cy
                        if _m_pos:
                            _m_pos.x = _old_px
                            _m_pos.y = _old_py
                    if _player_tile_snap is not None:
                        _old_ptx, _old_pty, _old_ppx, _old_ppy = _player_tile_snap
                        tile_move.current_tile_x = _old_ptx
                        tile_move.current_tile_y = _old_pty
                        if _player_pos:
                            _player_pos.x = _old_ppx
                            _player_pos.y = _old_ppy
                    # Se skill moveu o player (ex: Interceptar), notifica todos
                    if (tile_move.target_tile_x != _tx_before or
                            tile_move.target_tile_y != _ty_before):
                        _new_tx = tile_move.target_tile_x
                        _new_ty = tile_move.target_tile_y
                        # is_dash detectado: handler Interceptar seta tile_move.is_dash=True
                        _move_is_dash = getattr(tile_move, "is_dash", False)
                        # AOI_UPDATE para outros players no range
                        self._moved_this_tick.append({
                            "eid":     player_eid,
                            "tx":      _new_tx,
                            "ty":      _new_ty,
                            "from_tx": _tx_before,
                            "from_ty": _ty_before,
                            "is_dash": _move_is_dash,
                        })
                        # Correção direta ao próprio caster (AOI_UPDATE ignora self._my_eid)
                        # Armazena para _dispatch_tick_deltas enviar via ENTITY_MOVE direto.
                        # is_dash igual ao broadcast acima: sem isso, se a predição local
                        # do dash já tiver desistido (ex: bloqueado na posição antiga) e o
                        # player estiver andando normalmente quando esta correção chegar
                        # (servidor validou com sucesso numa posição mais nova — corrida
                        # entre MOVE e CAST_SKILL), o cliente trata como correção comum e
                        # faz snap instantâneo, cancelando o walk em andamento sem nenhuma
                        # animação de dash. Com is_dash, ele enfileira e anima certinho.
                        self._skill_position_corrections.append({
                            "player_eid": player_eid,
                            "tx":         _new_tx,
                            "ty":         _new_ty,
                            "is_dash":    _move_is_dash,
                        })
                        tile_move.current_tile_x = _new_tx
                        tile_move.current_tile_y = _new_ty
                except Exception as e:
                    import traceback
                    print(f"[Skill] ERRO ao processar {sid}: {e}")
                    traceback.print_exc()
                    self._skill_system._server_pending_spells = None
                    continue

            # Coleta dano causado + feedback de esquiva/miss para o alvo
            import components as _comp
            import systems as _sys
            _combat_svc = getattr(_sys, "_svc", {}).get("combat")
            _skill_outcome = getattr(_combat_svc, "last_outcome", "hit")

            results_targets = []
            for mob_eid, hp_before in hp_snapshot.items():
                cs = self.world.get_component(mob_eid, _comp.CombatStats)
                if not cs:
                    continue
                # Usa cs.current_hp real (pode ser negativo no golpe fatal)
                # para mostrar dano real no floating text, não o HP restante
                hp_real  = cs.current_hp               # pode ser negativo se matou
                hp_after = max(0, hp_real)             # para display da barra
                damage   = max(0, hp_before - hp_real) # dano real (inclui overkill)

                # Efeitos aplicados por esta skill neste mob
                _sfx_post  = self.world.get_component(mob_eid, _comp.StatusEffects)
                _eff_after = set(_sfx_post.effects.keys()) if _sfx_post else set()
                _applied   = list(_eff_after - effects_snapshot.get(mob_eid, set()))
                # Duração real de cada efeito novo — lida do StatusEffects que o handler acabou de
                # popular. Cliente usa isso para aplicar visualmente com o tempo correto.
                _eff_durs  = {
                    ef: round(_sfx_post.effects[ef].duration, 2)
                    for ef in _applied
                    if _sfx_post and ef in _sfx_post.effects
                } if _applied else {}

                def _make_result(outcome):
                    r = {"eid": mob_eid, "damage": damage, "outcome": outcome,
                         "hp_after": hp_after, "applied_effects": _applied}
                    if _eff_durs:
                        r["effect_durations"] = _eff_durs
                    return r

                if damage > 0:
                    results_targets.append(_make_result(_skill_outcome))
                elif mob_eid == tid and _skill_outcome in ("miss", "dodge", "parry", "block"):
                    results_targets.append(_make_result(_skill_outcome))
                elif _applied and mob_eid == tid:
                    results_targets.append(_make_result("hit"))

            # PvP: sincroniza HP + efeitos + rastreia dano para evitar FLT duplo via mob_delta.
            import components as _comp_pvp
            from components import CharacterStats as _CSvic
            for _pvp_r in results_targets:
                _pvp_eid = _pvp_r["eid"]
                if _pvp_eid in self._player_eids.values():
                    # Rastreia dano de skill para subtrair de mob_delta em _process_player_attacks
                    if _pvp_r["damage"] > 0:
                        _pvd = getattr(self, "_pvp_damage_this_tick", {})
                        _pvd[_pvp_eid] = _pvd.get(_pvp_eid, 0) + _pvp_r["damage"]
                    _vic_cs   = self.world.get_component(_pvp_eid, _comp_pvp.CombatStats)
                    _vic_char = self.world.get_component(_pvp_eid, _CSvic)
                    if _vic_cs:
                        _pvp_entry = {
                            "player_eid": _pvp_eid,
                            "xp": 0, "mob_eid": -1,
                            "rage": _vic_char.rage if _vic_char else 0,
                            "hp": _pvp_r["hp_after"], "hp_max": _vic_cs.max_hp,
                        }
                        if _pvp_r.get("applied_effects"):
                            _pvp_entry["applied_effects"]  = _pvp_r["applied_effects"]
                            _pvp_entry["effect_durations"] = _pvp_r.get("effect_durations", {})
                        self._pending_xp_deliveries.append(_pvp_entry)

            # Registra CD server-side APENAS se handler teve sucesso.
            # Registra CD efetivo (com reduções de talento) para que a validação futura
            # use o mesmo valor que o cliente recebeu — evita rejeição falsa por dessincronia.
            if _skill_ok and _sk_cd > 0:
                self._skill_last_used[_sk_key]    = _now_srv
                _eff_cd_store = getattr(skill_obj, "current_cooldown", _sk_cd)
                self._skill_effective_cd[_sk_key] = _eff_cd_store

            # Envia SKILL_RESULT sempre: sucesso (com dano/efeitos) OU falha (failed=True).
            # Cliente usa failed=True para restaurar carga consumida localmente + limpar pending.
            if results_targets or _skill_ok:
                _eff_cd    = getattr(skill_obj, "current_cooldown", skill_obj.cooldown) if skill_obj else None
                _has_cast  = getattr(skill_obj, "cast_time", 0.0) > 0
                _result_entry = {
                    "caster_eid": player_eid,
                    "sid":        sid,
                    "targets":    results_targets,
                    "cooldown":   _eff_cd,
                    "failed":     False,
                }
                # Cast com tempo: este SKILL_RESULT é só confirmação — GCD sem som/CD.
                # Som, cooldown real e dano chegam no SKILL_RESULT da completion.
                if _has_cast and _skill_ok:
                    _result_entry["cast_started"] = True

                # ── SKILL_EFFECT: emite evento de apresentação (som/VFX) ─────────
                _caster_tx = tile_move.current_tile_x
                _caster_ty = tile_move.current_tile_y
                if _has_cast and _skill_ok:
                    # Cast com tempo aceito → som de cast_start (ex: arrow nock, cancao)
                    self._skill_effects_this_tick.append({
                        "sid": sid, "event": "cast_start",
                        "caster_eid": player_eid, "tx": _caster_tx, "ty": _caster_ty,
                    })
                elif _skill_ok and not _has_cast:
                    # Skill instantânea → som de impact ou miss imediatamente
                    _sfx_event = "miss" if _skill_outcome in ("miss", "dodge", "parry", "block") else "impact"
                    self._skill_effects_this_tick.append({
                        "sid": sid, "event": _sfx_event,
                        "caster_eid": player_eid, "tx": _caster_tx, "ty": _caster_ty,
                        "target_eid": tid,
                    })
                # Procs que precisam ser sincronizados para o cliente
                # Só inclui se o proc ACABOU de ser gerado neste cast (não carga pré-existente).
                from components import CharacterStats as _CSproc
                _char_proc = self.world.get_component(player_eid, _CSproc)
                _free_exec_after = getattr(_char_proc, "free_executar_charges", 0) if _char_proc else 0
                if _free_exec_after > _free_exec_before:
                    _result_entry["assassino_proc"] = True
                self._skill_results_this_tick.append(_result_entry)
            else:
                # Handler retornou False — inclui motivo de rejeição para o cliente exibir
                _fail_reason = getattr(self._skill_system, "_last_warn", "")
                self._skill_system._last_warn = ""  # limpa para próxima skill
                self._skill_results_this_tick.append({
                    "caster_eid": player_eid,
                    "sid":        sid,
                    "targets":    [],
                    "cooldown":   0,
                    "failed":     True,
                    "reason":     _fail_reason,
                })
                # Desfaz predição visual de movimento no cliente (ver comentário
                # de _MOVEMENT_PREDICTED_SKILLS) — usa o canal já existente de
                # correção de posição (skill_rejected), mesmo mecanismo usado
                # quando o handler TEM sucesso e move o player.
                if sid in _MOVEMENT_PREDICTED_SKILLS:
                    self._skill_position_corrections.append({
                        "player_eid": player_eid,
                        "tx": tile_move.current_tile_x,
                        "ty": tile_move.current_tile_y,
                        "rejected": True,
                    })

            # Sincroniza rage/mana/hp do player após a skill
            from components import CharacterStats as _CShr
            _char_after = self.world.get_component(player_eid, _CShr)
            _cs_after   = self.world.get_component(player_eid, CombatStats)
            if _char_after:
                _player_hp_after = (_cs_after.current_hp if _cs_after else _player_hp_before)
                _heal_amount     = max(0, _player_hp_after - _player_hp_before)
                _mana_after_handler = _char_after.mana
                _stat_entry = {
                    "player_eid": player_eid,
                    "xp":         0,
                    "mob_eid":    -1,
                    "rage":       _char_after.rage,
                }
                # Mana só é incluída se o handler realmente a deduziu (skills instantâneas).
                # Spells com cast_time deduzem mana na completion — não enviar aqui ou o
                # STATS_UPDATE chegaria ao cliente antes do cast terminar e pareceria que
                # a mana foi subtraída antes do cast completar.
                if _mana_after_handler != _mana_before_handler:
                    _stat_entry["mana"] = _mana_after_handler
                # Concentração nunca enviada aqui (nem em sucesso nem em falha).
                # Sync ocorre SOMENTE na completion em spell_completion_processor.py,
                # quando o custo foi de fato deduzido pelo servidor.
                # Enviar ao detectar falha causaria queda visual imediata por drift
                # de regen entre cliente (60 FPS) e servidor (30 TPS).
                # Se o player se curou, inclui hp atual e quantidade curada para o cliente
                if _heal_amount > 0 and _cs_after:
                    _stat_entry["hp"]          = _cs_after.current_hp
                    _stat_entry["hp_max"]       = _cs_after.max_hp
                    _stat_entry["heal_amount"]  = _heal_amount
                    _stat_entry["heal_sid"]     = sid
                    # Broadcast do HP para outros players no AOI verem a barra atualizar
                    self._player_hp_broadcasts_this_tick.append({
                        "eid":    player_eid,
                        "hp":     _cs_after.current_hp,
                        "hp_max": _cs_after.max_hp,
                    })
                self._pending_xp_deliveries.append(_stat_entry)

            # Limpa referência ao pending_spell_completions após cada request
            self._skill_system._server_pending_spells = None
