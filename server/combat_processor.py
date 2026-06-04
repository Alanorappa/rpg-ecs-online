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
        from systems import deal_damage
        from components import CombatState, CombatStats, TileMovement, Enemy, PendingDeath
        from utils import chebyshev

        # ── Player → Mob ───────────────────────────────────────────────────
        for session_id, player_eid in list(self._player_eids.items()):
            cs = self.world.get_component(player_eid, CombatState)
            if not cs or cs.target_entity_id == -1:
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
            player_cs    = self.world.get_component(player_eid, CombatStats)
            attack_range = 7 if getattr(player_cs, "is_ranged", False) else 1
            _srv_dist = chebyshev(player_tm.current_tile_x, player_tm.current_tile_y,
                                  target_tm.current_tile_x, target_tm.current_tile_y)
            if _srv_dist > attack_range:
                continue

            # Cooldown de ataque (inicializa em 0 para atacar imediatamente no primeiro range)
            timer = self._attack_timers.get(session_id, 0.0) - dt
            if timer > 0:
                self._attack_timers[session_id] = timer
                continue
            interval = player_cs.attack_interval if player_cs else 2.0
            self._attack_timers[session_id] = interval

            # ── deal_damage() do offline: mesma fórmula, armor, crit, dodge ──
            hp_before        = target_cs.current_hp
            dead, _outcome   = deal_damage(player_eid, target_eid, "physical")
            # Usa current_hp real (pode ser negativo no golpe fatal) para dano correto
            hp_real  = target_cs.current_hp      # pode ser negativo se matou
            hp_after = max(0, hp_real)           # para display da barra de HP
            damage   = max(0, hp_before - hp_real)  # dano real (inclui overkill)

            # Ataque disparou → enter_combat + rage (copiado de PlayerInputSystem:1491-1492)
            # Rage é gerada SEMPRE que o ataque dispara — mesmo em miss (igual ao offline)
            from stat_fns import enter_combat as _enter_combat
            from components import CharacterStats as _CS_char
            player_cst  = self.world.get_component(player_eid, CombatState)
            player_char = self.world.get_component(player_eid, _CS_char)
            if player_cst:
                _enter_combat(player_cst)
            # Rage no servidor — mantém sincronizado para validação de skills
            # O cliente gera rage localmente (igual ao offline); aqui apenas atualizamos
            # o valor no ECS do servidor para que os handlers de skill possam validar
            if player_char and player_char.class_id == "guerreiro":
                player_char.rage = min(getattr(player_char, 'max_rage', 100),
                                       player_char.rage + 5)

            # Punho no Queixo (Cavaleiro): incrementar contador por auto-ataque.
            # Espelha PlayerInputSystem._increment_pnq_counter; cliente também chama
            # via COMBAT_RESULT.source=="auto", mas o servidor precisa manter charges
            # sincronizados para validar CAST_SKILL punho_no_queixo.
            _pnq_hit = _outcome not in ("miss", "dodge", "parry", "block")
            if _pnq_hit and player_cs and player_cs.pnq_enabled and player_char:
                from components import PlayerSkills as _PKSv
                from skill_config import SKILL_CATALOG as _SC_pnq
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

            if damage > 0:
                log = self._mob_damage_log.setdefault(target_eid, {})
                log[player_eid] = log.get(player_eid, 0) + damage

            self._combat_this_tick.append({
                "attacker": player_eid,
                "target":   target_eid,
                "damage":   damage,
                "outcome":  _outcome,   # retornado diretamente por deal_damage (sem singleton)
                "hp_after": hp_after,
                "source":   "auto",
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
        from components import AIControlled as _AIAtk, PendingDeath as _PD
        _mob_attacker_of: dict[int, int] = {}
        for _mb in self._mob_eids:
            _ai_r = self.world.get_component(_mb, _AIAtk)
            if _ai_r and _ai_r.state in ("ATTACKING", "CHASING") and _ai_r.target_eid != -1:
                _mob_attacker_of[_ai_r.target_eid] = _mb

        # Consome avoidances (parry/dodge/miss) de mob→player coletadas em CombatSystem.
        from systems import _svc as _svc_cp
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

        for peid, hp_before in player_hp_snapshot.items():
            pcs = self.world.get_component(peid, CombatStats)
            if not pcs:
                continue
            hp_now    = pcs.current_hp
            sfx_dmg   = self._sfx_damage_players.get(peid, 0)
            mob_delta = (hp_before - sfx_dmg) - hp_now   # dano exclusivo de mobs/projéteis

            if mob_delta > 0:
                attacker_mob_eid = _mob_attacker_of.get(peid, -1)
                # hp_after = HP intermediário APÓS o ataque do mob, ANTES do DOT tick.
                # Garante que o cliente aplique: mob_attack (HP cai N) → DOT (HP cai M)
                # em vez de: DOT (HP cai N+M) → mob_attack (HP não muda).
                # Adicionado em _pending_mob_attacks para ser emitido ANTES dos eventos de DOT.
                self._pending_mob_attacks.append({
                    "attacker": attacker_mob_eid,
                    "target":   peid,
                    "damage":   mob_delta,
                    "outcome":  "hit",
                    "hp_after": max(0, hp_before - mob_delta),  # HP antes do DOT
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
        from systems import deal_damage
        from components import CombatState, CombatStats, TileMovement, PendingDeath
        from utils import chebyshev

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
        attack_range = 7 if getattr(attacker_cs, "is_ranged", False) else 1

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
        from stat_fns import enter_combat as _ec_pvp
        attacker_cst = self.world.get_component(attacker_eid, CombatState)
        if attacker_cst:
            _ec_pvp(attacker_cst)

        # COMBAT_RESULT → AOI_UPDATE para ambos os clientes
        self._combat_this_tick.append({
            "attacker": attacker_eid,
            "target":   victim_eid,
            "damage":   damage,
            "outcome":  _outcome,
            "hp_after": hp_after,
            "source":   "auto",
        })

        # HP sync direto para a vítima (STATS_UPDATE individual)
        from components import CharacterStats as _CSvic
        vic_char = self.world.get_component(victim_eid, _CSvic)
        self._pending_xp_deliveries.append({
            "player_eid": victim_eid,
            "xp":         0,
            "mob_eid":    -1,
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
