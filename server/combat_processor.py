"""
server/combat_processor.py
Mixin para WorldServer: auto-attacks (player→mob, mob→player).
"""
from __future__ import annotations


class CombatProcessorMixin:

    def _process_player_attacks(self, dt: float,
                               player_hp_snapshot: dict[int, int]) -> None:
        """
        Processa auto-attacks dos jogadores em mobs.

        Usa deal_damage() do offline — mesma fórmula de dano, armor reduction,
        crit, dodge, parry. Mob→player é tratado detectando a variação de HP
        após EnemyAISystem rodar (EnemyAISystem chama deal_damage internamente).
        """
        from engine.world_systems import deal_damage
        from engine.components import CombatState, CombatStats, TileMovement, Enemy, PendingDeath
        from engine.utils import chebyshev, is_action_locked

        # ── Player → Mob ───────────────────────────────────────────────────
        for session_id, player_eid in list(self._player_eids.items()):
            cs = self.world.get_component(player_eid, CombatState)
            if not cs or cs.target_entity_id == -1:
                continue

            # Espelha o gate offline (PlayerInputSystem usa can_act()): pressionar
            # uma skill seta is_pursuing=True e dispara AUTO_ATTACK pro alvo, mas o
            # auto-attack não pode disparar durante o cast — senão ele aplica dano
            # (e portanto aggro) antes/junto do dano da própria skill.
            # is_action_locked cobre sleep/disoriented/polymorph — can_act() não
            # inclui esses 3 (vivem em StatusEffects); sem o segundo check, um
            # player adormecido/desorientado/polimorfizado continuava auto-atacando.
            if not cs.can_act() or is_action_locked(self.world, player_eid):
                continue

            target_eid = cs.target_entity_id
            if target_eid not in self._mob_eids:
                # PvP: alvo pode ser outro jogador
                if target_eid in self._player_eids.values() and getattr(self, "pvp_enabled", False):
                    # Suprime auto-attack PvP se uma skill disparou neste tick para este player:
                    # evita FLT duplicado (skill + auto em simultâneo) quando o player
                    # está perseguindo e usa skill ao mesmo tempo.
                    _skill_fired = any(
                        r.get("caster_eid") == player_eid and not r.get("failed")
                        and not r.get("cast_started")
                        for r in self._skill_results_this_tick
                    )
                    if not _skill_fired:
                        self._process_pvp_attack(player_eid, target_eid, session_id, dt)
                else:
                    cs.target_entity_id = -1
                continue

            target_cs = self.world.get_component(target_eid, CombatStats)
            if not target_cs or target_cs.current_hp <= 0:
                cs.target_entity_id = -1
                continue

            # Valida range
            player_tm = self.world.get_component(player_eid, TileMovement)
            target_tm = self.world.get_component(target_eid, TileMovement)
            if not player_tm or not target_tm:
                continue
            player_cs = self.world.get_component(player_eid, CombatStats)

            # is_ranged (CombatStats) é um flag ESTÁTICO por classe
            # (CLASS_MELEE_OVERRIDES — arqueiro=True sempre), nunca
            # reavaliado contra o equipamento real. Servidor autoritativo:
            # só entra em modo ranged se a classe É ranged E tem um ARCO de
            # verdade equipado agora — sem arma, ou com arma melee
            # equipada, cai pro MESMO caminho de dano melee (deal_damage)
            # que guerreiro/mago já usam, em vez de simplesmente pular o
            # auto-attack do player inteiro (`continue`) como antes. Ver
            # PROBLEMAS_ARQUITETURA.md.
            _class_is_ranged = getattr(player_cs, "is_ranged", False)
            _bow_cp = _qv_cp = None
            _has_bow_equipped = False
            if _class_is_ranged:
                from engine.components import Equipment as _EqCP
                _eq_cp  = self.world.get_component(player_eid, _EqCP)
                _bow_cp = _eq_cp.slots.get("mainhand") if _eq_cp else None
                _qv_cp  = _eq_cp.slots.get("offhand")  if _eq_cp else None
                _has_bow_equipped = (_bow_cp is not None
                                    and getattr(_bow_cp, "subtype", "") == "Bow")
            _is_ranged_p = _class_is_ranged and _has_bow_equipped

            # Range: ranged usa o cast_range REAL do arco equipado (Arco
            # Curto=7, do Caçador=8, Élfico=9...) — nunca hardcoded. Antes
            # era fixo em 7 pra qualquer arco: um arqueiro com arco de
            # cast_range>7 (ex. Arco Élfico=9) tinha o cliente parando a
            # perseguição na distância certa (bow_range real, ver
            # systems.py::_process_archer_combat) mas o SERVOR rejeitando
            # todo golpe por estar "fora de alcance" (7 fixo) — o cliente
            # tocava o som de disparo e descontava flecha otimisticamente
            # (nunca espera confirmação, ver comentário "100% server-driven"
            # em _process_archer_combat) mas o ataque nunca completava de
            # verdade, pois o COMBAT_RESULT nunca chegava. Ver
            # PROBLEMAS_ARQUITETURA.md.
            attack_range = (getattr(_bow_cp, "cast_range", 0) or 7) if _is_ranged_p else 1

            # Ranged (arco de verdade equipado): requer is_pursuing + aljava
            # com flechas no offhand — sem isso, NÃO ataca (nem ranged nem
            # melee: tem arco em mãos, só falta munição — "Use Recarregar" é
            # o fluxo esperado, não cair pra soco). Sem arco: nem entra
            # aqui, já segue reto pro dano melee mais abaixo.
            if _is_ranged_p:
                if not cs.is_pursuing:
                    from debug.archer_debug import ADBG_SERVER as _ADBG_np
                    _ADBG_np.log_block(player_eid, target_eid, "not_pursuing")
                    continue
                if not _qv_cp or getattr(_qv_cp, "item_type", "") != "quiver" \
                        or _qv_cp.arrow_count < 1:
                    from debug.archer_debug import ADBG_SERVER as _ADBG_nq
                    _ADBG_nq.log_block(player_eid, target_eid, "no_quiver_or_empty",
                                       arrows=getattr(_qv_cp, "arrow_count", None))
                    continue

            _srv_dist = chebyshev(player_tm.current_tile_x, player_tm.current_tile_y,
                                  target_tm.current_tile_x, target_tm.current_tile_y)
            if _is_ranged_p:
                from debug.archer_debug import ADBG_SERVER as _ADBG_attempt
                _ADBG_attempt.log_periodic("ATTEMPT", player_eid, target_eid, interval=1.0,
                                  dist=_srv_dist, attack_range=attack_range,
                                  cooldown=round(self._attack_timers.get(session_id, 0.0), 2),
                                  arrows=_qv_cp.arrow_count if _qv_cp else None,
                                  is_pursuing=cs.is_pursuing)
            if _srv_dist > attack_range:
                if _is_ranged_p:
                    from debug.archer_debug import ADBG_SERVER as _ADBG_range
                    _ADBG_range.log_block(player_eid, target_eid, "out_of_range",
                                          dist=_srv_dist, attack_range=attack_range)
                continue

            # Linha de visão (só ranged — melee é range=1, obstrução não se
            # aplica na prática): obstáculo BLOQUEIA O ATAQUE INTEIRO, antes
            # de qualquer efeito colateral — nem cooldown é consumido (mesmo
            # tratamento do check de range acima: o tiro nem foi tentado).
            # Decisão do usuário (revisão da Decisão 21, ARQUITETURA_ONLINE.md):
            # a primeira versão deste fix tratava obstáculo como "miss" (dano
            # 0, mas som+projétil ainda apareciam, só sem consumir flecha/agro)
            # — o usuário corrigiu: o comportamento certo é NADA acontecer
            # (sem som, sem projétil, sem flecha, sem agro), igual estar fora
            # de alcance. O guard dentro de _server_apply_ranged_physical
            # (retorna "miss") continua existindo só como rede de segurança
            # pras SKILLS que reusam essa função (Tiro Repulsivo etc. já
            # commitaram visualmente o lançamento antes do hit resolver —
            # não dá pra "descommitar" do mesmo jeito que o auto-attack, que
            # ainda nem tinha tocado som nenhum neste ponto).
            if _is_ranged_p:
                from engine.components import Tilemap as _TMap_cp
                from debug.archer_debug import ADBG_SERVER as _ADBG_los
                _los_map_cp    = self.get_entity_map(target_eid)
                _los_bundle_cp = self._map_bundles.get(_los_map_cp) if _los_map_cp else None
                _los_tmap_cp   = (self.world.get_component(_los_bundle_cp.tilemap_entity, _TMap_cp)
                                  if _los_bundle_cp is not None else None)
                if _los_tmap_cp is not None:
                    from engine.world_systems import EnemyAISystem as _EAIS_cp
                    _has_los_cp = _EAIS_cp._has_line_of_sight(
                            _los_tmap_cp, player_tm.current_tile_x, player_tm.current_tile_y,
                            target_tm.current_tile_x, target_tm.current_tile_y)
                    _ADBG_los.log_los(player_eid, target_eid, _has_los_cp,
                                      p_tile=(player_tm.current_tile_x, player_tm.current_tile_y),
                                      t_tile=(target_tm.current_tile_x, target_tm.current_tile_y))
                    if not _has_los_cp:
                        _ADBG_los.log_block(player_eid, target_eid, "los_blocked")
                        # Bug real encontrado via debug log (09/07/2026): o
                        # `continue` aqui pula o enter_combat() que só rodava
                        # DEPOIS do cooldown (linha ~217, "Ataque disparou →
                        # enter_combat"). Antes deste fix de LOS existir, um
                        # tiro "bloqueado" ainda chegava até lá (virava "miss"
                        # server-side) e mantinha o timer de combate vivo. Com
                        # o bloqueio adiantado (decisão do usuário — não gerar
                        # som/projétil), um alvo alternando dentro/fora de
                        # cobertura por 6s+ (OUT_OF_COMBAT_DURATION,
                        # components.py) nunca mais chamava enter_combat,
                        # `combat_timer` expirava sozinho
                        # (_tick_combat_timer, core_systems.py) e
                        # `is_pursuing` virava False silenciosamente — o
                        # arqueiro "parava de atacar do nada", e como o
                        # client nunca fica sabendo que o servidor desistiu
                        # (is_pursuing não é resincronizado), o client
                        # continuava a predição local otimista de flecha pra
                        # sempre (o bug de "desconta sem gerar flecha").
                        # Continuar tentando atirar (mesmo bloqueado) CONTA
                        # como estar em combate — só o disparo em si (som/
                        # projétil/flecha/agro) fica bloqueado.
                        _player_cst_los = self.world.get_component(player_eid, CombatState)
                        if _player_cst_los:
                            from engine.stat_fns import enter_combat as _enter_combat_los
                            _enter_combat_los(_player_cst_los)
                        continue
                else:
                    # Tilemap não encontrado pro mapa do alvo — LOS não checada
                    # de verdade (deixa passar); logar pra saber se é a causa
                    # do "para do nada" (register_map_services_for não rodou?).
                    _ADBG_los.log("LOS", player_eid, target_eid, has_los="NO_TILEMAP")

            # Cooldown de ataque (inicializa em 0 para atacar imediatamente no primeiro range)
            timer = self._attack_timers.get(session_id, 0.0) - dt
            if timer > 0:
                self._attack_timers[session_id] = timer
                if _is_ranged_p:
                    from debug.archer_debug import ADBG_SERVER as _ADBG_cd
                    _ADBG_cd.log_block(player_eid, target_eid, "cooldown", remaining=round(timer, 2))
                continue
            interval = player_cs.attack_interval if player_cs else 2.0
            self._attack_timers[session_id] = interval

            # ── Dano: ranged usa _server_apply_ranged_physical (fórmula física completa
            # com crit/block/armor); melee usa deal_damage normal. ──
            hp_before = target_cs.current_hp
            if _is_ranged_p:
                from debug.archer_debug import ADBG_SERVER as _ADBG_fire
                from engine.components import AIControlled as _AIC_dbg
                _arrows_before_fire = _qv_cp.arrow_count if _qv_cp else None
                _mob_ai_dbg = self.world.get_component(target_eid, _AIC_dbg)
                _ai_state_before_fire = _mob_ai_dbg.state if _mob_ai_dbg else None
                _ADBG_fire.log_fire_ok(player_eid)
                dead, _outcome, damage = self._server_apply_ranged_physical(
                    player_eid, target_eid, is_ability=False)
                hp_real  = target_cs.current_hp
                hp_after = max(0, hp_real)
                _arrows_after_fire = _qv_cp.arrow_count if _qv_cp else None
                _ADBG_fire.log("FIRE", player_eid, target_eid,
                               outcome=_outcome, damage=damage)
                if _arrows_before_fire != _arrows_after_fire:
                    _ADBG_fire.log("ARROW", player_eid, target_eid,
                                   arrows_before=_arrows_before_fire, arrows_after=_arrows_after_fire)
                _ai_state_after_fire = _mob_ai_dbg.state if _mob_ai_dbg else None
                if _ai_state_before_fire != _ai_state_after_fire:
                    _ADBG_fire.log("AGGRO", player_eid, target_eid,
                                   state_before=_ai_state_before_fire, state_after=_ai_state_after_fire)
            else:
                dead, _outcome = deal_damage(player_eid, target_eid, "physical")
                hp_real  = target_cs.current_hp
                hp_after = max(0, hp_real)
                damage   = max(0, hp_before - hp_real)

            # Ataque disparou → enter_combat + rage (copiado de PlayerInputSystem:1491-1492)
            # Rage é gerada SEMPRE que o ataque dispara — mesmo em miss (igual ao offline)
            from engine.stat_fns import enter_combat as _enter_combat
            from engine.components import CharacterStats as _CS_char
            player_cst  = self.world.get_component(player_eid, CombatState)
            player_char = self.world.get_component(player_eid, _CS_char)
            if player_cst:
                _enter_combat(player_cst)
            # Rage no servidor — único produtor autoritativo. Todo ganho é empurrado
            # ao cliente via STATS_UPDATE (o cliente online não gera rage localmente;
            # dois timers de ataque independentes drifavam e a hotbar acendia com
            # rage que o servidor não tinha — ver PROBLEMAS_ARQUITETURA.md).
            if player_char and player_char.class_id == "guerreiro":
                _rage_before = player_char.rage
                player_char.rage = min(getattr(player_char, 'max_rage', 100),
                                       player_char.rage + 5)
                if player_char.rage != _rage_before:
                    self.queue_stats_update({
                        "player_eid": player_eid,
                        "rage":       player_char.rage,
                    })

            # Punho no Queixo (Cavaleiro): incrementar contador por auto-ataque.
            # Espelha PlayerInputSystem._increment_pnq_counter; cliente também chama
            # via COMBAT_RESULT.source=="auto", mas o servidor precisa manter charges
            # sincronizados para validar CAST_SKILL punho_no_queixo.
            _pnq_hit = _outcome not in ("miss", "dodge", "parry", "block")
            if _pnq_hit and player_cs and player_cs.pnq_enabled and player_char:
                from engine.components import PlayerSkills as _PKSv
                from content.skill_config import SKILL_CATALOG as _SC_pnq
                _ps_pnq = self.world.get_component(player_eid, _PKSv)
                if _ps_pnq:
                    # Procura skill no hotbar; cria lazily se não estiver (talento alocado
                    # mas skill não adicionada ao hotbar — servidor ainda precisa rastrear)
                    _sk_p = next((sk for sk in _ps_pnq.skills
                                  if sk and sk.skill_id == "punho_no_queixo"), None)
                    if _sk_p is None:
                        _sk_p = _PKSv._make_skill("punho_no_queixo", _SC_pnq)
                        if _sk_p is not None:
                            try:
                                idx = _ps_pnq.skills.index(None)
                                _ps_pnq.skills[idx] = _sk_p
                            except ValueError:
                                _ps_pnq.skills.append(_sk_p)
                    # Usa _skill_last_used para verificar CD — current_cooldown no
                    # objeto Skill nunca é decrementado no servidor (SkillSystem.update
                    # não roda). Sem esta correção, charges param de acumular após
                    # o primeiro uso.
                    import time as _t_pnq
                    _pnq_cd      = getattr(_sk_p, "cooldown", 0.0) if _sk_p else 0.0
                    _pnq_elapsed = _t_pnq.time() - self._skill_last_used.get(
                                       (player_eid, "punho_no_queixo"), 0.0)
                    if _sk_p and _pnq_elapsed >= _pnq_cd:
                        player_char.pnq_counter += 1
                        if player_char.pnq_counter >= 3:
                            player_char.pnq_counter = 0
                            if _sk_p.charges < _sk_p.max_charges:
                                _sk_p.charges += 1

            # _mob_damage_log agora é populado de forma centralizada por
            # WorldServer._log_mob_damage_hit, injetado em CombatSystem
            # (melee, via deal_damage) e em _apply_final_damage (ranged, via
            # _server_apply_ranged_physical acima) — escrever aqui também
            # contaria o MESMO golpe em dobro. Ver PROBLEMAS_ARQUITETURA.md.

            self._combat_this_tick.append({
                "attacker": player_eid,
                "target":   target_eid,
                "damage":   damage,
                "outcome":  _outcome,   # retornado diretamente por deal_damage (sem singleton)
                "hp_after": hp_after,
                "source":   "auto",
                # Diz ao cliente se ESTE golpe especifico foi ranged ou melee —
                # arqueiro sem arco/com arma melee cai pro mesmo "auto" mas com
                # is_ranged=False. Cliente NAO deve re-derivar isso de class_id
                # sozinho (remote_entity_handlers.py so sabe a classe do
                # remoto, nunca o equipamento) — ver PROBLEMAS_ARQUITETURA.md.
                "is_ranged": _is_ranged_p,
            })

            if dead:
                # deal_damage adicionou PendingDeath — ServerDeathHandler processa
                # no mesmo tick (chamado após _process_player_attacks).
                # Apenas limpa alvo e timer de ataque; remoção fica com o handler.
                cs.target_entity_id = -1
                self._attack_timers.pop(session_id, None)

        # ── Mob → Player: detectado via variação de HP após EnemyAISystem ──
        # EnemyAISystem já chamou deal_damage() nos players. Basta comparar
        # o snapshot de HP capturado antes dos sistemas rodarem.
        # IMPORTANTE: subtraímos _sfx_damage_players para não emitir COMBAT_RESULT
        # duplicado de ticks de DoT/HoT que StatusEffectSystem já reportou separado.

        # Pré-constrói reverse map {player_eid → mob_atacante} UMA VEZ (O(mobs)),
        # em vez de O(mobs×players_danificados) no loop abaixo.
        from engine.components import AIControlled as _AIAtk, PendingDeath as _PD
        _mob_attacker_of: dict[int, int] = {}
        for _mb in self._mob_eids:
            _ai_r = self.world.get_component(_mb, _AIAtk)
            if _ai_r and _ai_r.state in ("ATTACKING", "CHASING") and _ai_r.target_eid != -1:
                _mob_attacker_of[_ai_r.target_eid] = _mb
                # Atualiza cache do último atacante para fallback no mesmo tick
                if not hasattr(self, "_last_mob_attacker"):
                    self._last_mob_attacker = {}
                self._last_mob_attacker[_ai_r.target_eid] = _mb

        # Consome avoidances (parry/dodge/miss) de mob→player coletadas em CombatSystem.
        from engine.world_systems import _svc as _svc_cp
        _combat_sys_cp = _svc_cp.get('combat')
        if _combat_sys_cp and _combat_sys_cp.mob_avoidance_events:
            for _atk_av, _tgt_av, _out_av, _hp_av in _combat_sys_cp.mob_avoidance_events:
                self._pending_mob_attacks.append({
                    "attacker": _atk_av,
                    "target":   _tgt_av,
                    "damage":   0,
                    "outcome":  _out_av,
                    "hp_after": _hp_av,
                    "source":   "auto",
                })
            _combat_sys_cp.mob_avoidance_events.clear()

        if not hasattr(self, "_last_mob_attacker"):
            self._last_mob_attacker: dict[int, int] = {}

        for peid, hp_before in player_hp_snapshot.items():
            pcs = self.world.get_component(peid, CombatStats)
            if not pcs:
                continue
            hp_now    = pcs.current_hp
            sfx_dmg   = self._sfx_damage_players.get(peid, 0)
            # Subtrai dano PvP (skills + auto-ataque player→player) para não
            # confundir com dano de mob. Sem isso, dano PvP gerava um segundo
            # COMBAT_RESULT "de mob" → FLT duplicado na tela do atacante.
            pvp_dmg   = self._pvp_damage_this_tick.get(peid, 0)
            mob_delta = (hp_before - sfx_dmg - pvp_dmg) - hp_now

            if mob_delta > 0:
                attacker_mob_eid = _mob_attacker_of.get(peid, -1)
                # Fallback: mob pode ter mudado de estado no mesmo tick após atacar.
                # Usa o último mob rastreado para este player se disponível.
                if attacker_mob_eid == -1:
                    attacker_mob_eid = self._last_mob_attacker.get(peid, -1)
                elif attacker_mob_eid != -1:
                    self._last_mob_attacker[peid] = attacker_mob_eid

                self._pending_mob_attacks.append({
                    "attacker": attacker_mob_eid,
                    "target":   peid,
                    "damage":   mob_delta,
                    "outcome":  "hit",
                    "hp_after": max(0, hp_now),
                    "source":   "auto",
                })

            # Morte: qualquer fonte (mob ou DoT) que zerou HP neste tick
            if hp_now <= 0 and hp_before > 0:
                # Remove PendingDeath adicionado pelo deal_damage do EnemyAI
                try:
                    self.world.remove_component(peid, _PD)
                except Exception:
                    pass
                self._handle_player_death(peid)

    # ── PvP: player → player ──────────────────────────────────────────────────

    def _process_pvp_attack(self, attacker_eid: int, victim_eid: int,
                             session_id: str, dt: float) -> None:
        """Auto-attack de player em outro player (PvP).

        Requer is_pursuing=True: ataque só ocorre quando o jogador está
        ativamente perseguindo o alvo (clique direito), não apenas selecionado.
        Usa server_tile_x/y se disponível para range check mais preciso.
        """
        from engine.world_systems import deal_damage
        from engine.components import CombatState, CombatStats, TileMovement, PendingDeath
        from engine.utils import chebyshev

        # Requer is_pursuing — previne auto-ataque acidental com alvo só selecionado
        attacker_cst = self.world.get_component(attacker_eid, CombatState)
        if not attacker_cst or not attacker_cst.is_pursuing:
            return

        victim_cs = self.world.get_component(victim_eid, CombatStats)
        if not victim_cs or victim_cs.current_hp <= 0:
            return

        attacker_tm = self.world.get_component(attacker_eid, TileMovement)
        victim_tm   = self.world.get_component(victim_eid,   TileMovement)
        if not attacker_tm or not victim_tm:
            return

        attacker_cs = self.world.get_component(attacker_eid, CombatStats)
        # Mesmo critério dinâmico do PvE (_process_player_attacks): só é
        # ranged se a classe É ranged E tem arco de verdade equipado agora —
        # arqueiro sem arco (ou com arma melee) briga em PvP na distância
        # melee, igual qualquer outra classe.
        _pvp_class_ranged = getattr(attacker_cs, "is_ranged", False)
        _pvp_has_bow = False
        if _pvp_class_ranged:
            from engine.components import Equipment as _EqPvp
            _eq_pvp  = self.world.get_component(attacker_eid, _EqPvp)
            _bow_pvp = _eq_pvp.slots.get("mainhand") if _eq_pvp else None
            _pvp_has_bow = _bow_pvp is not None and getattr(_bow_pvp, "subtype", "") == "Bow"
        # Mesmo fix do PvE: usa cast_range REAL do arco, nunca 7 fixo.
        attack_range = (getattr(_bow_pvp, "cast_range", 0) or 7) if (_pvp_class_ranged and _pvp_has_bow) else 1

        # Usa server_tile_x/y (posição autoritativa) quando disponível;
        # fallback para current_tile (última posição confirmada)
        _a_tx = getattr(attacker_tm, "server_tile_x", 0) or attacker_tm.current_tile_x
        _a_ty = getattr(attacker_tm, "server_tile_y", 0) or attacker_tm.current_tile_y
        _v_tx = getattr(victim_tm,   "server_tile_x", 0) or victim_tm.current_tile_x
        _v_ty = getattr(victim_tm,   "server_tile_y", 0) or victim_tm.current_tile_y
        dist = chebyshev(_a_tx, _a_ty, _v_tx, _v_ty)
        if dist > attack_range:
            return

        # Cooldown compartilhado com PvE
        timer = self._attack_timers.get(session_id, 0.0) - dt
        if timer > 0:
            self._attack_timers[session_id] = timer
            return
        interval = attacker_cs.attack_interval if attacker_cs else 2.0
        self._attack_timers[session_id] = interval

        # Aplica dano
        hp_before      = victim_cs.current_hp
        dead, _outcome = deal_damage(attacker_eid, victim_eid, "physical")
        hp_real  = victim_cs.current_hp
        hp_after = max(0, hp_real)
        damage   = max(0, hp_before - hp_real)

        # Enter combat no atacante
        from engine.stat_fns import enter_combat as _ec_pvp
        attacker_cst = self.world.get_component(attacker_eid, CombatState)
        if attacker_cst:
            _ec_pvp(attacker_cst)

        # Rage do atacante (Guerreiro) — mesmo ganho do PvE. Antes só o cliente
        # gerava rage em PvP (local), o servidor nunca — toda skill com custo de
        # rage era rejeitada mesmo com a barra cheia no cliente.
        from engine.components import CharacterStats as _CS_pvp
        _atk_char = self.world.get_component(attacker_eid, _CS_pvp)
        if _atk_char and _atk_char.class_id == "guerreiro":
            _rage_before_pvp = _atk_char.rage
            _atk_char.rage = min(getattr(_atk_char, 'max_rage', 100),
                                 _atk_char.rage + 5)
            if _atk_char.rage != _rage_before_pvp:
                self.queue_stats_update({
                    "player_eid": attacker_eid,
                    "rage":       _atk_char.rage,
                })

        # Rastreia dano PvP para subtrair de mob_delta (evita FLT duplo)
        self._pvp_damage_this_tick[victim_eid] = (
            self._pvp_damage_this_tick.get(victim_eid, 0) + damage
        )

        # COMBAT_RESULT → AOI_UPDATE para ambos os clientes
        self._combat_this_tick.append({
            "attacker": attacker_eid,
            "target":   victim_eid,
            "damage":   damage,
            "outcome":  _outcome,
            "hp_after": hp_after,
            "source":   "auto",
            "is_ranged": _pvp_class_ranged and _pvp_has_bow,
        })

        # HP sync direto para a vítima (STATS_UPDATE individual)
        from engine.components import CharacterStats as _CSvic
        vic_char = self.world.get_component(victim_eid, _CSvic)
        self.queue_stats_update({
            "player_eid": victim_eid,
            "rage":       vic_char.rage if vic_char else 0,
            "hp":         hp_after,
            "hp_max":     victim_cs.max_hp,
        })

        # Morte por PvP
        if dead:
            # Remove PendingDeath de deal_damage (handler de player é separado)
            try:
                self.world.remove_component(victim_eid, PendingDeath)
            except Exception:
                pass
            self._handle_player_death(victim_eid)
            # Ataque concluído — limpa alvo do atacante
            attacker_cst2 = self.world.get_component(attacker_eid, CombatState)
            if attacker_cst2:
                attacker_cst2.target_entity_id = -1
            self._attack_timers.pop(session_id, None)
