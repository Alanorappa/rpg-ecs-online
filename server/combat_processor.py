"""
server/combat_processor.py
Mixin para WorldServer: auto-attacks (player→mob, mob→player).
"""
from __future__ import annotations


class CombatProcessorMixin:

    def _resolve_auto_attack(self, attacker_eid: int, target_eid: int, session_id: str, dt: float,
                             attacker_cs, attacker_tm, target_cs, target_tm) -> "dict | None":
        """Ponto único: TUDO que decide se um auto-attack acontece e o que
        ele causa — independente do alvo ser MOB ou PLAYER (16/08/2026,
        unificação pedida pelo usuário depois de reportar arqueiro sem
        aljava atirando em PvP; ver PROBLEMAS_ARQUITETURA.md). Antes,
        `_process_player_attacks` (mob) e `_process_pvp_attack` (player)
        eram 2 implementações PARALELAS — cada checagem nova (LOS,
        munição, fórmula de arco) só era adicionada no lado PvE e nunca
        replicada no PvP, então os 2 caminhos divergiam em silêncio.
        Pesquisa de referência (AzerothCore): `Unit::RewardRage` vive no
        caminho ÚNICO de dano da classe base (Player/Creature herdam do
        mesmo Unit) — não existe branch PvE-only pra recurso/combo lá,
        confirma que Raiva/Punho no Queixo devem morar AQUI, não
        condicionados a "alvo é mob".

        `is_pursuing` SEMPRE exigido (antes: só ranged no PvE, sempre no
        PvP — unificado pra sempre exigir, achado investigando, não o
        bug relatado pelo usuário).

        Retorna `None` se o ataque foi BLOQUEADO (perseguindo/munição/
        LOS/cooldown) — nada aconteceu, sem custo nenhum, chamador só
        faz `continue`. Retorna dict `{dead, outcome, damage, hp_after,
        is_ranged}` se disparou (dano já aplicado, Raiva/Punho no Queixo
        já processados)."""
        from engine.world_systems import deal_damage
        from engine.components import CombatState, Equipment, CharacterStats, PlayerSkills
        from engine.utils import chebyshev
        from debug.archer_debug import ADBG_SERVER

        attacker_cst = self.world.get_component(attacker_eid, CombatState)
        if not attacker_cst or not attacker_cst.is_pursuing:
            return None

        # is_ranged (CombatStats) é um flag ESTÁTICO por classe
        # (CLASS_MELEE_OVERRIDES — arqueiro=True sempre), nunca
        # reavaliado contra o equipamento real. Servidor autoritativo: só
        # entra em modo ranged se a classe É ranged E tem um ARCO de
        # verdade equipado agora — sem arco, ou com arma melee equipada,
        # cai pro MESMO caminho de dano melee (deal_damage) que
        # guerreiro/mago já usam.
        _class_is_ranged = getattr(attacker_cs, "is_ranged", False)
        _bow = _qv = None
        _has_bow_equipped = False
        if _class_is_ranged:
            _eq  = self.world.get_component(attacker_eid, Equipment)
            _bow = _eq.slots.get("mainhand") if _eq else None
            _qv  = _eq.slots.get("offhand")  if _eq else None
            _has_bow_equipped = _bow is not None and getattr(_bow, "subtype", "") == "Bow"
        _is_ranged = _class_is_ranged and _has_bow_equipped

        # Range: ranged usa o cast_range REAL do arco equipado (Arco
        # Curto=7, do Caçador=8, Élfico=9...) — nunca hardcoded.
        attack_range = (getattr(_bow, "cast_range", 0) or 7) if _is_ranged else 1

        # Ranged (arco de verdade equipado): requer aljava com flechas no
        # offhand — sem isso, NÃO ataca (nem ranged nem melee: tem arco
        # em mãos, só falta munição — "Use Recarregar" é o fluxo
        # esperado, não cair pra soco). Sem arco: nem entra aqui, já
        # segue reto pro dano melee mais abaixo.
        if _is_ranged:
            if not _qv or getattr(_qv, "item_type", "") != "quiver" or _qv.arrow_count < 1:
                ADBG_SERVER.log_block(attacker_eid, target_eid, "no_quiver_or_empty",
                                      arrows=getattr(_qv, "arrow_count", None))
                return None

        # Usa server_tile_x/y (posição autoritativa, sincronizada via
        # ENTITY_MOVE) quando disponível; fallback pra current_tile
        # (última posição confirmada) — mesma precisão que só o PvP tinha
        # antes da unificação (16/08/2026: dá pra trazer pro caminho
        # compartilhado sem risco pro lado mob, já que mob nunca popula
        # server_tile_x, então cai no MESMO fallback de sempre).
        _a_tx = getattr(attacker_tm, "server_tile_x", 0) or attacker_tm.current_tile_x
        _a_ty = getattr(attacker_tm, "server_tile_y", 0) or attacker_tm.current_tile_y
        _t_tx = getattr(target_tm,   "server_tile_x", 0) or target_tm.current_tile_x
        _t_ty = getattr(target_tm,   "server_tile_y", 0) or target_tm.current_tile_y

        dist = chebyshev(_a_tx, _a_ty, _t_tx, _t_ty)
        if dist > attack_range:
            if _is_ranged:
                ADBG_SERVER.log_block(attacker_eid, target_eid, "out_of_range",
                                      dist=dist, attack_range=attack_range)
            return None

        # Linha de visão (só ranged — melee é range=1, obstrução não se
        # aplica na prática): obstáculo BLOQUEIA O ATAQUE INTEIRO, antes
        # de qualquer efeito colateral — nem cooldown é consumido. Ver
        # PROBLEMAS_ARQUITETURA.md pro histórico da decisão (obstáculo =
        # nada acontece, não um "miss" com som/projétil).
        if _is_ranged:
            from engine.components import Tilemap
            _los_map    = self.get_entity_map(target_eid)
            _los_bundle = self._map_bundles.get(_los_map) if _los_map else None
            _los_tilemap = (self.world.get_component(_los_bundle.tilemap_entity, Tilemap)
                            if _los_bundle is not None else None)
            if _los_tilemap is not None:
                from engine.world_systems import EnemyAISystem
                _has_los = EnemyAISystem._has_line_of_sight(
                    _los_tilemap, _a_tx, _a_ty, _t_tx, _t_ty)
                ADBG_SERVER.log_los(attacker_eid, target_eid, _has_los)
                if not _has_los:
                    ADBG_SERVER.log_block(attacker_eid, target_eid, "los_blocked")
                    # Continuar tentando atirar (mesmo bloqueado) CONTA
                    # como estar em combate — só o disparo em si (som/
                    # projétil/flecha/agro) fica bloqueado (ver
                    # PROBLEMAS_ARQUITETURA.md).
                    from engine.stat_fns import enter_combat as _ec_los
                    _ec_los(attacker_cst)
                    return None

        # Cooldown de ataque (inicializa em 0 para atacar imediatamente no primeiro range)
        timer = self._attack_timers.get(session_id, 0.0) - dt
        if timer > 0:
            self._attack_timers[session_id] = timer
            if _is_ranged:
                ADBG_SERVER.log_block(attacker_eid, target_eid, "cooldown", remaining=round(timer, 2))
            return None
        interval = attacker_cs.attack_interval if attacker_cs else 2.0
        self._attack_timers[session_id] = interval

        # ── Dano: ranged usa _server_apply_ranged_physical (fórmula física
        # completa com crit/block/armor/perícia de arco/consumo de
        # flecha); melee usa deal_damage normal. ──
        hp_before = target_cs.current_hp
        if _is_ranged:
            ADBG_SERVER.log_fire_ok(attacker_eid)
            dead, outcome, damage = self._server_apply_ranged_physical(
                attacker_eid, target_eid, is_ability=False)
            hp_after = max(0, target_cs.current_hp)
        else:
            dead, outcome = deal_damage(attacker_eid, target_eid, "physical")
            hp_real  = target_cs.current_hp
            hp_after = max(0, hp_real)
            damage   = max(0, hp_before - hp_real)

        # Ataque disparou → enter_combat + Raiva + Punho no Queixo —
        # SEMPRE, independente do alvo ser mob ou player (antes só
        # rodava no braço PvE; ver docstring desta função).
        from engine.stat_fns import enter_combat
        enter_combat(attacker_cst)
        attacker_char = self.world.get_component(attacker_eid, CharacterStats)
        if attacker_char and attacker_char.class_id == "guerreiro":
            _rage_before = attacker_char.rage
            attacker_char.rage = min(getattr(attacker_char, "max_rage", 100), attacker_char.rage + 5)
            if attacker_char.rage != _rage_before:
                self.queue_stats_update({"player_eid": attacker_eid, "rage": attacker_char.rage})

        # Punho no Queixo (Cavaleiro): incrementa contador por golpe de
        # verdade (não miss/dodge/parry/block). Espelha
        # PlayerInputSystem._increment_pnq_counter; cliente também chama
        # via COMBAT_RESULT.source=="auto", mas o servidor precisa manter
        # charges sincronizados pra validar CAST_SKILL punho_no_queixo.
        _pnq_hit = outcome not in ("miss", "dodge", "parry", "block")
        if _pnq_hit and attacker_cs and attacker_cs.pnq_enabled and attacker_char:
            from content.skill_config import SKILL_CATALOG as _SC_pnq
            _ps_pnq = self.world.get_component(attacker_eid, PlayerSkills)
            if _ps_pnq:
                _sk_p = next((sk for sk in _ps_pnq.skills
                             if sk and sk.skill_id == "punho_no_queixo"), None)
                if _sk_p is None:
                    _sk_p = PlayerSkills._make_skill("punho_no_queixo", _SC_pnq)
                    if _sk_p is not None:
                        try:
                            idx = _ps_pnq.skills.index(None)
                            _ps_pnq.skills[idx] = _sk_p
                        except ValueError:
                            _ps_pnq.skills.append(_sk_p)
                # Usa _skill_last_used pra verificar CD — current_cooldown
                # no objeto Skill nunca é decrementado no servidor
                # (SkillSystem.update não roda). Sem isso, charges param
                # de acumular após o primeiro uso.
                import time as _t_pnq
                _pnq_cd      = getattr(_sk_p, "cooldown", 0.0) if _sk_p else 0.0
                _pnq_elapsed = _t_pnq.time() - self._skill_last_used.get(
                                   (attacker_eid, "punho_no_queixo"), 0.0)
                if _sk_p and _pnq_elapsed >= _pnq_cd:
                    attacker_char.pnq_counter += 1
                    if attacker_char.pnq_counter >= 3:
                        attacker_char.pnq_counter = 0
                        if _sk_p.charges < _sk_p.max_charges:
                            _sk_p.charges += 1

        return {"dead": dead, "outcome": outcome, "damage": damage,
                "hp_after": hp_after, "is_ranged": _is_ranged}

    def _process_player_attacks(self, dt: float,
                               player_hp_snapshot: dict[int, int]) -> None:
        """
        Processa auto-attacks dos jogadores em mobs.

        Usa deal_damage() do offline — mesma fórmula de dano, armor reduction,
        crit, dodge, parry. Mob→player é tratado detectando a variação de HP
        após EnemyAISystem rodar (EnemyAISystem chama deal_damage internamente).
        """
        from engine.world_systems import deal_damage
        from engine.components import CombatState, CombatStats, TileMovement, Enemy, PendingDeath, GhostState
        from engine.utils import chebyshev, is_action_locked

        # ── Player → Mob ───────────────────────────────────────────────────
        for session_id, player_eid in list(self._player_eids.items()):
            cs = self.world.get_component(player_eid, CombatState)
            if not cs or cs.target_entity_id == -1:
                continue

            # Corpo (morto) ou espírito: nunca ataca — mesmo que o cliente
            # esteja com bug visual ou tente burlar can_act() mandando um
            # AUTO_ATTACK novo depois de morrer (mesmo padrão de defesa já
            # usado em skill_processor.py — a morte real limpa
            # target_entity_id em _handle_player_death, mas um AUTO_ATTACK
            # NOVO do cliente pode setar de novo antes do próximo tick).
            gst = self.world.get_component(player_eid, GhostState)
            if gst is not None and gst.is_dead:
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
                # PvP: alvo pode ser outro jogador — o CONTEXTO decide
                # (duelo/zona/arena, via can_engage → _pvp_allowed_between;
                # players são amigáveis por default, 16/07/2026). Antes:
                # flag global pvp_enabled ("mundo inteiro é zona PvP").
                from engine.faction_system import can_engage as _can_engage_pvp
                if (target_eid in self._player_eids.values()
                        and _can_engage_pvp(self.world, player_eid, target_eid)):
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

            # Facção "amigavel" nunca é alvo de auto-attack — rede de segurança
            # além do gate em set_player_target() (já recusa o alvo na origem),
            # cobre qualquer forma de um target_entity_id amigavel chegar aqui.
            from engine.faction_system import can_engage as _can_engage_paa
            if not _can_engage_paa(self.world, player_eid, target_eid):
                cs.target_entity_id = -1
                continue

            player_tm = self.world.get_component(player_eid, TileMovement)
            target_tm = self.world.get_component(target_eid, TileMovement)
            if not player_tm or not target_tm:
                continue
            player_cs = self.world.get_component(player_eid, CombatStats)

            # Toda a decisão de SE o ataque acontece e COM QUE fórmula
            # (perseguindo, arco/munição/LOS, cooldown, dano ranged vs
            # melee, Raiva, Punho no Queixo) mora em `_resolve_auto_attack`
            # — ponto único, compartilhado com `_process_pvp_attack`
            # (16/08/2026, ver docstring de `_resolve_auto_attack`).
            result = self._resolve_auto_attack(player_eid, target_eid, session_id, dt,
                                               player_cs, player_tm, target_cs, target_tm)
            if result is None:
                continue

            self._combat_this_tick.append({
                "attacker": player_eid,
                "target":   target_eid,
                "damage":   result["damage"],
                "outcome":  result["outcome"],
                "hp_after": result["hp_after"],
                "source":   "auto",
                # Diz ao cliente se ESTE golpe especifico foi ranged ou melee —
                # arqueiro sem arco/com arma melee cai pro mesmo "auto" mas com
                # is_ranged=False. Cliente NAO deve re-derivar isso de class_id
                # sozinho (remote_entity_handlers.py so sabe a classe do
                # remoto, nunca o equipamento) — ver PROBLEMAS_ARQUITETURA.md.
                "is_ranged": result["is_ranged"],
            })

            # Evento de quest "auto_attack_hit" (Fase Q1, 25/07/2026) — só
            # acerto de verdade, nunca miss/dodge/parry/block. PRÓPRIO de
            # alvo MOB (lê EntityIdentity de mob) — sem equivalente em
            # PvP, skill já tem seu próprio evento "use_skill".
            _pnq_hit = result["outcome"] not in ("miss", "dodge", "parry", "block")
            if _pnq_hit:
                from engine.components import EntityIdentity as _EI_aa
                from engine.quest_events import fire as _qfire_aa
                _ident_aa = self.world.get_component(target_eid, _EI_aa)
                if _ident_aa:
                    _qfire_aa("auto_attack_hit", player_eid=player_eid,
                              name=_ident_aa.name, race=_ident_aa.race)

            if result["dead"]:
                # deal_damage (melee) adiciona PendingDeath sozinho;
                # _server_apply_ranged_physical (ranged) também já adiciona
                # com o killer certo, no fim da própria função (ver
                # spell_completion_processor.py) — nenhum dos dois precisa
                # de ajuda aqui. ServerDeathHandler processa no mesmo tick
                # (chamado após _process_player_attacks). Apenas limpa alvo
                # e timer de ataque; remoção fica com o handler.
                cs.target_entity_id = -1
                self._attack_timers.pop(session_id, None)

        # ── Mob → Player: detectado via variação de HP após EnemyAISystem ──
        # EnemyAISystem já chamou deal_damage() nos players. Basta comparar
        # o snapshot de HP capturado antes dos sistemas rodarem.
        # IMPORTANTE: subtraímos _sfx_damage_players para não emitir COMBAT_RESULT
        # duplicado de ticks de DoT/HoT que StatusEffectSystem já reportou separado.

        # Pré-constrói reverse map {player_eid → mob_atacante} UMA VEZ (O(mobs)),
        # em vez de O(mobs×players_danificados) no loop abaixo.
        from engine.components import (AIControlled as _AIAtk, PendingDeath as _PD,
                                       Tower as _TowerAtk, Minion as _MinionAtk)
        _mob_attacker_of: dict[int, int] = {}
        for _mb in self._mob_eids:
            _ai_r = self.world.get_component(_mb, _AIAtk)
            if _ai_r and _ai_r.state in ("ATTACKING", "CHASING") and _ai_r.target_eid != -1:
                _mob_attacker_of[_ai_r.target_eid] = _mb
                # Atualiza cache do último atacante para fallback no mesmo tick
                if not hasattr(self, "_last_mob_attacker"):
                    self._last_mob_attacker = {}
                self._last_mob_attacker[_ai_r.target_eid] = _mb
                continue
            # Torre (29/07/2026): não tem AIControlled (ver engine/
            # components.py::Tower), então ficava INVISÍVEL a este reverse
            # map — dano de torre em player sempre resolvia attacker=-1
            # (ou pior, caía no fallback _last_mob_attacker e herdava o
            # atacante ERRADO de um mob real anterior), quebrando a
            # atribuição de som/log de combate (bug real relatado pelo
            # usuário: torre de flecha tocava som de attack_melee — na
            # verdade estava tocando o som do ÚLTIMO mob real que bateu
            # no player, não o da torre).
            _tw_r = self.world.get_component(_mb, _TowerAtk)
            if _tw_r and _tw_r.current_target_eid != -1:
                _mob_attacker_of[_tw_r.current_target_eid] = _mb
                if not hasattr(self, "_last_mob_attacker"):
                    self._last_mob_attacker = {}
                self._last_mob_attacker[_tw_r.current_target_eid] = _mb
                continue
            # Minion (30/07/2026): mesma razão de Torre — sem AIControlled,
            # invisível a este reverse map sem este bloco (mesma classe de
            # bug: atribuição errada/perdida de attacker em ataque de
            # minion contra player).
            _mn_r = self.world.get_component(_mb, _MinionAtk)
            if _mn_r and _mn_r.current_target_eid != -1:
                _mob_attacker_of[_mn_r.current_target_eid] = _mb
                if not hasattr(self, "_last_mob_attacker"):
                    self._last_mob_attacker = {}
                self._last_mob_attacker[_mn_r.current_target_eid] = _mb

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
                # Lê o killer ANTES de remover o PendingDeath — repassado
                # pro crédito de kill/assist dentro de _handle_player_death
                # (16/08/2026, unificação — este era o caminho que sempre
                # apagava o PendingDeath ANTES do ServerDeathHandler genérico
                # rodar, fazendo o crédito de kill nunca acontecer de
                # verdade em partida real; ver PROBLEMAS_ARQUITETURA.md).
                _pd_comp = self.world.get_component(peid, _PD)
                _killer_for_death = _pd_comp.killer_entity_id if _pd_comp else -1
                try:
                    self.world.remove_component(peid, _PD)
                except Exception:
                    pass
                self._handle_player_death(peid, killer_eid=_killer_for_death)

    # ── PvP: player → player ──────────────────────────────────────────────────

    def _process_pvp_attack(self, attacker_eid: int, victim_eid: int,
                             session_id: str, dt: float) -> None:
        """Auto-attack de player em outro player (PvP) — a decisão de SE
        o ataque acontece e COM QUE fórmula (perseguindo, arco real/
        munição/LOS, cooldown, Raiva, Punho no Queixo) é 100%
        compartilhada com o braço PvE via `_resolve_auto_attack`
        (16/08/2026 — antes esta função reimplementava uma versão mais
        pobre, sem munição/LOS/fórmula de arco, que nunca ficava em
        sincronia com o PvE; bug real relatado pelo usuário: arqueiro
        sem aljava ainda atirava em PvP). Aqui só sobra o que é PRÓPRIO
        de alvo PLAYER: existência/HP da vítima, sync de HP dela
        (STATS_UPDATE), e morte tratada por `_handle_player_death` (não
        pelo ServerDeathHandler genérico, que é pra mob)."""
        from engine.components import CombatState, CombatStats, TileMovement, PendingDeath

        victim_cs = self.world.get_component(victim_eid, CombatStats)
        if not victim_cs or victim_cs.current_hp <= 0:
            return

        attacker_tm = self.world.get_component(attacker_eid, TileMovement)
        victim_tm   = self.world.get_component(victim_eid,   TileMovement)
        if not attacker_tm or not victim_tm:
            return

        attacker_cs = self.world.get_component(attacker_eid, CombatStats)

        result = self._resolve_auto_attack(attacker_eid, victim_eid, session_id, dt,
                                           attacker_cs, attacker_tm, victim_cs, victim_tm)
        if result is None:
            return

        # Rastreio de _pvp_damage_this_tick (evita FLT duplo via mob_delta)
        # agora é centralizado em WorldServer._damage_tracker_composite —
        # o dano já disparou apply_damage_core com killer_eid válido
        # dentro de _resolve_auto_attack, que já registrou isso (Fase C,
        # 23/07/2026). Registrar aqui TAMBÉM duplicaria a entrada no dict.

        # COMBAT_RESULT → AOI_UPDATE para ambos os clientes
        self._combat_this_tick.append({
            "attacker": attacker_eid,
            "target":   victim_eid,
            "damage":   result["damage"],
            "outcome":  result["outcome"],
            "hp_after": result["hp_after"],
            "source":   "auto",
            "is_ranged": result["is_ranged"],
        })

        # HP sync direto para a vítima (STATS_UPDATE individual)
        from engine.components import CharacterStats as _CSvic
        vic_char = self.world.get_component(victim_eid, _CSvic)
        self.queue_stats_update({
            "player_eid": victim_eid,
            "rage":       vic_char.rage if vic_char else 0,
            "hp":         result["hp_after"],
            "hp_max":     victim_cs.max_hp,
        })

        # Morte por PvP
        if result["dead"]:
            # Remove PendingDeath de deal_damage/_server_apply_ranged_physical
            # (handler de player é separado, não o ServerDeathHandler genérico)
            try:
                self.world.remove_component(victim_eid, PendingDeath)
            except Exception:
                pass
            self._handle_player_death(victim_eid, killer_eid=attacker_eid)
            # Ataque concluído — limpa alvo do atacante
            attacker_cst2 = self.world.get_component(attacker_eid, CombatState)
            if attacker_cst2:
                attacker_cst2.target_entity_id = -1
            self._attack_timers.pop(session_id, None)
