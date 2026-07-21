"""
remote_entity_handlers.py — Mixin com a gestão de entidades remotas
(mobs e players controlados pelo servidor): spawn, despawn, movimento,
sincronização de efeitos/combate e desenho de HP bars/corpses/players remotos.
Separado de game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.world, self._my_eid, self._net e os demais atributos referenciados aqui.
"""
import pygame

from engine.components import Position
from engine.tileset import TILE_SIZE
from ui.combat_log import LOG
from ui.sound_manager import SOUNDS


class RemoteEntityHandlers:
    # Skills do arqueiro cujo dano é entregue por flecha (projétil físico) —
    # impacto deve usar arrow_impact_* em vez dos sons genéricos de melee.
    _ARROW_SKILL_IDS = {"picada_escorpiao", "flecha_reiterada", "tiro_repulsivo"}

    def _play_attacker_mob_sound(self, server_attacker: int, _lx: float, _ly: float) -> bool:
        """Toca o som de ataque do MOB atacante (attack_melee/ranged/magic via
        MobSounds), posicional com falloff pela distância.

        Sons definidos em mob_definitions.py → componente MobSounds (fonte única).
        Nenhum nome de mob ou som hardcoded aqui.

        Retorna True se o atacante é um mob (rastreado ou não localmente) —
        suprime o fallback hit_normal do caller, evitando som de espada errado.
        Retorna False apenas se o atacante é um player remoto (PvP).
        """
        from engine.components import MobSounds as _MobSounds, AIControlled as _AICtrl
        _atk_mob_local = self._remote_mobs.get(server_attacker)
        if _atk_mob_local is None:
            # Mob fora do AOI local, attacker=-1 (origem não identificada no servidor)
            # ou ataque de DoT cujo mob já despawnou.
            # Se o atacante NÃO é um player remoto conhecido → é um mob → suprime hit_normal.
            return server_attacker not in self._remote_players
        _atk_pos = self.world.get_component(_atk_mob_local, Position)
        if not _atk_pos:
            return True  # Mob existe no ECS mas sem posição — suprime hit_normal
        _atk_snd = self.world.get_component(_atk_mob_local, _MobSounds)
        _atk_ai  = self.world.get_component(_atk_mob_local, _AICtrl)
        if _atk_ai and _atk_ai.entity_class in ("Mage", "Mago", "Warlock", "Bruxo"):
            _atk_ev = "attack_magic"
        elif _atk_ai and _atk_ai.is_ranged:
            _atk_ev = "attack_ranged"
        else:
            _atk_ev = "attack_melee"
        SOUNDS.play_mob_sounds_at(_atk_snd, _atk_ev, _atk_pos.x, _atk_pos.y, _lx, _ly,
                                  base=0.85, dedup_key=str(server_attacker))
        return True

    def _resolve_archer_attack(self, cr: dict, server_attacker: int, source: str):
        """Detecta se um ataque deve nascer como flecha visual (PlayerProjectile)
        em vez de dano instantâneo: atacante é arqueiro (local ou remoto) e o
        ataque é auto-attack ou uma skill de flecha.

        Retorna (is_archer_arrow, attacker_eid, is_self_attacker). attacker_eid
        é a entidade local de quem disparou (player local ou player remoto).
        """
        from engine.components import CharacterStats as _CHS_ar, RemoteControlled as _RC_ar
        _char_ar = self.world.get_component(self.player_entity, _CHS_ar)
        _sid_ar  = cr.get("sid", "")
        _is_proj_damage_ar = cr.get("is_proj_damage", False)
        _attacker_remote = self._remote_players.get(server_attacker)
        _is_remote_archer = False
        if _attacker_remote is not None:
            _rc_ar = self.world.get_component(_attacker_remote, _RC_ar)
            _is_remote_archer = _rc_ar is not None and _rc_ar.class_id == "arqueiro"
        _is_self_archer = (server_attacker == self._my_eid
                            and _char_ar is not None
                            and _char_ar.class_id == "arqueiro")
        _is_archer = _is_self_archer or _is_remote_archer
        if source == "auto":
            # Auto-attack: o servidor decide ranged x melee golpe-a-golpe (arma
            # REAL equipada agora — arqueiro sem arco/com arma melee cai pra
            # melee), via combat_processor.py, e manda o veredito em is_ranged.
            # Nunca re-derivar isso so de class_id aqui: o cliente (sobretudo
            # pra arqueiro REMOTO) não tem visibilidade do equipamento atual
            # do atacante — só o servidor sabe. Sem este campo (combat result
            # antigo/DoT), assume True (comportamento anterior). Ver
            # PROBLEMAS_ARQUITETURA.md.
            _is_arrow = not _is_proj_damage_ar and _is_archer and cr.get("is_ranged", True)
        else:
            _is_arrow = not _is_proj_damage_ar and _is_archer and _sid_ar in self._ARROW_SKILL_IDS
        _attacker_eid = self.player_entity if _is_self_archer else _attacker_remote
        return _is_arrow, _attacker_eid, _is_self_archer

    def _spawn_archer_auto_arrow(self, attacker_eid, is_self_attacker: bool,
                                  target_local_eid: int, target_pos, _lx: float, _ly: float,
                                  color: tuple = (101, 67, 33)) -> None:
        """Cria a flecha visual (PlayerProjectile) do auto-attack do arqueiro e
        toca os sons de saque/disparo (cheios para o player local, posicionais
        com falloff para arqueiro remoto)."""
        import random as _rand_arrow
        from engine.components import PlayerProjectile as _PParrow
        if attacker_eid is None:
            return
        _ppos = self.world.get_component(attacker_eid, Position)
        if _ppos is None:
            return
        _arrow_id = self.world.create_entity()
        self.world.add_component(_arrow_id, Position(
            x=_ppos.x, y=_ppos.y, prev_x=_ppos.x, prev_y=_ppos.y))
        self.world.add_component(_arrow_id, _PParrow(
            spell_id="arrow",
            attacker_id=attacker_eid,
            target_id=target_local_eid,
            speed=700.0,
            dmg_weapon_pct=1.0,
            dmg_sp_coeff=0.0,
            color=color,
            damage_type="physical",
            target_last_x=target_pos.x,
            target_last_y=target_pos.y,
        ))
        from debug.archer_debug import ADBG_CLIENT as _ADBG_c_spawn
        _ADBG_c_spawn.log("SOUND", attacker_eid, target_local_eid,
                          which="release", is_self=is_self_attacker, proj_id=_arrow_id)
        if is_self_attacker:
            if _rand_arrow.random() < 0.35:
                SOUNDS.play_random(["arrow_draw_1", "arrow_draw_2"], channel_group=(8, 9))
            SOUNDS.play_random(["arrow_release_1", "arrow_release_2"], channel_group=(10, 11))
        else:
            if _rand_arrow.random() < 0.35:
                SOUNDS.play_random_at(["arrow_draw_1", "arrow_draw_2"],
                                      _ppos.x, _ppos.y, _lx, _ly, base=0.5)
            SOUNDS.play_random_at(["arrow_release_1", "arrow_release_2"],
                                  _ppos.x, _ppos.y, _lx, _ly, base=0.5)

    def _apply_combat_result(self, cr: dict) -> None:
        """Aplica resultado de combate do servidor: HP + texto flutuante + sons.

        Cores idênticas ao offline (systems.py:716-727):
          auto-attack normal: branco (220,220,220)    auto-attack crit:   branco (255,255,255) + is_crit=True → animação grande
          skill normal:       amarelo (255,220,0)
          skill crit:         amarelo (255,220,50) + is_crit=True → animação grande
          player dano:        vermelho (220,80,80) + is_crit para animação
        """
        from engine.components import Position, CombatStats, RemoteControlled
        from ui.floating_text import FLT
        from ui.sound_manager import SOUNDS
        server_target   = cr.get("target",   -1)
        server_attacker = cr.get("attacker", -1)
        damage          = cr.get("damage",    0)
        outcome         = cr.get("outcome",   "hit")
        hp_after        = cr.get("hp_after",  -1)
        source          = cr.get("source",    "auto")
        is_crit         = outcome == "crit"
        is_regen        = outcome == "regen"
        is_ability      = source == "skill"  # skill=amarelo, auto=branco (igual offline)
        # DoT/HoT ticks: source != "auto"/"skill" — sem som (bleed, poison, burn, regen…)
        _is_dot_hot     = source not in ("auto", "skill")

        col_regen = (100, 220, 100)

        _lx, _ly = self._player_world_pos()
        from engine.components import MobSounds as _MobSounds, EntityIdentity as _EIdent

        # DEBUG C15/C16: registra distância attacker/target → player local
        # vs AOI_RADIUS, pra achar sons/FLT vindos de fora da área visível.
        if not _is_dot_hot:
            from debug.aoi_debug import AOI_DBG as _AOI_DBG_cr, DBG_ENABLED as _DBG_EN_cr
            if _DBG_EN_cr:
                from shared.constants import AOI_RADIUS as _AOI_R_cr
                import math as _math_cr
                _aoi_px = _AOI_R_cr * TILE_SIZE
                _atk_eid_dbg = (self.player_entity if server_attacker == self._my_eid
                                else self._remote_players.get(server_attacker)
                                or self._remote_mobs.get(server_attacker))
                _tgt_eid_dbg = (self.player_entity if server_target == self._my_eid
                                else self._remote_players.get(server_target)
                                or self._remote_mobs.get(server_target))
                _atk_pos_dbg = self.world.get_component(_atk_eid_dbg, Position) if _atk_eid_dbg is not None else None
                _tgt_pos_dbg = self.world.get_component(_tgt_eid_dbg, Position) if _tgt_eid_dbg is not None else None
                _atk_dist = _math_cr.hypot(_atk_pos_dbg.x - _lx, _atk_pos_dbg.y - _ly) if _atk_pos_dbg else None
                _tgt_dist = _math_cr.hypot(_tgt_pos_dbg.x - _lx, _tgt_pos_dbg.y - _ly) if _tgt_pos_dbg else None
                _AOI_DBG_cr.log(
                    "COMBAT_RESULT_RECV",
                    attacker=server_attacker, attacker_eid=_atk_eid_dbg, attacker_dist=_atk_dist,
                    target=server_target, target_eid=_tgt_eid_dbg, target_dist=_tgt_dist,
                    aoi_px=_aoi_px, source=source, sid=cr.get("sid"), outcome=outcome, damage=damage,
                )

        # ── Mob foi atacado (player local ou remoto → mob) ────────────
        local_eid = self._remote_mobs.get(server_target)
        # Fallback: mob já despawnou antes do SKILL_RESULT chegar — exibe FLT na última
        # posição conhecida (ghost cache populado no despawn) para não perder o FLT fatal.
        if local_eid is None and cr.get("is_proj_damage") and damage > 0:
            _fb = self._mob_ghost_pos.get(server_target)
            if _fb:
                FLT.add(str(damage), _fb[0], _fb[1], (255, 220, 0), "normal")
        if local_eid is not None:
            # Determina se é ataque de flecha ANTES do HP update para poder diferir
            _sid_cr = cr.get("sid", "")
            _is_archer_arrow, _attacker_local_remote, _is_self_archer_attacker = \
                self._resolve_archer_attack(cr, server_attacker, source)
            _is_archer_auto = _is_archer_arrow  # alias mantém compatibilidade abaixo

            if _is_archer_arrow:
                from debug.archer_debug import ADBG_CLIENT as _ADBG_c_recv
                _ADBG_c_recv.log("RECV", server_attacker, server_target,
                                 outcome=outcome, damage=damage, hp_after=hp_after,
                                 is_self=_is_self_archer_attacker)

            # HP: atualização imediata apenas para ataques não-projéteis.
            # Flechas diferem para o momento de colisão (deferred_hp_updates em _on_hit).
            if hp_after >= 0 and not _is_archer_arrow:
                from engine.components import RemoteEntityMeta as _REM_cr
                _meta_cr = self.world.get_component(local_eid, _REM_cr)
                if _meta_cr:
                    _meta_cr.hp = hp_after
            # Sincroniza slow no mob local: aplica efeito "slow" ao StatusEffects do mob
            # para que StatusEffectSystem mantenha slow_mult correto E expire naturalmente.
            # Sem isso, o mob no cliente move em velocidade normal enquanto servidor tem slow.
            _mob_slow_mult = cr.get("mob_slow_mult")
            if _mob_slow_mult is not None:
                from engine.components import StatusEffects as _SFXcr, ActiveEffect as _AEcr
                _sfx_cr = self.world.get_component(local_eid, _SFXcr)
                if _sfx_cr is None:
                    _sfx_cr = _SFXcr()
                    self.world.add_component(local_eid, _sfx_cr)
                _slow_mag = max(0.05, float(_mob_slow_mult))
                _existing_slow = _sfx_cr.get("slow")
                if _existing_slow is not None:
                    # Usa o valor mais restritivo (menor = mais lento)
                    _existing_slow.magnitude = min(_existing_slow.magnitude, _slow_mag)
                    _existing_slow.duration  = 6.0  # renova duração
                else:
                    _sfx_cr.effects["slow"] = _AEcr("slow", 6.0, _slow_mag, 0.0)
            _mob_snd = self.world.get_component(local_eid, _MobSounds)
            pos = self.world.get_component(local_eid, Position)

            # Auto-attack do arqueiro é 100% server-driven: a flecha nasce aqui, ao
            # confirmar o COMBAT_RESULT, nunca por timer local (igual Bola de Fogo
            # nasce só no is_completion). Evita a corrida em que o servidor mata o
            # mob antes do timer client zerar e a flecha nunca chega a existir —
            # o golpe fatal ficava "invisível" (sem flecha, sem FLT, sem HP update).
            if source == "auto" and _is_archer_arrow and pos is not None:
                self._spawn_archer_auto_arrow(_attacker_local_remote, _is_self_archer_attacker,
                                              local_eid, pos, _lx, _ly)
                # Desconto REAL da aljava local acontece só AQUI agora — mesmo
                # evento que cria a flecha visual, nunca antecipado (ver
                # ui/systems.py::_process_archer_combat, bug real 10/07/2026:
                # cooldown local não congelava igual ao do servidor durante
                # bloqueio, causando descontos "fantasma" sem tiro real por
                # trás). Espelha a regra do servidor
                # (spell_completion_processor.py::_server_apply_ranged_physical):
                # a flecha é gasta sempre que o tiro é autorizado e resolvido —
                # inclusive em miss/dodge/parry (quem atira e erra ainda gastou
                # a flecha; correção do usuário 10/07/2026, versão anterior só
                # consumia em dano>0). Só "evade" (alvo em modo evasão/RETURNING)
                # não consome — nesse caso o servidor nem chega a soltar a
                # flecha de verdade. Só o PRÓPRIO player tem Equipment/aljava
                # local.
                if _is_self_archer_attacker and outcome != "evade":
                    from engine.components import Equipment as _Eq_arrow
                    _eq_arrow = self.world.get_component(self.player_entity, _Eq_arrow)
                    _qv_arrow = _eq_arrow.slots.get("offhand") if _eq_arrow else None
                    if _qv_arrow is not None and getattr(_qv_arrow, "item_type", "") == "quiver":
                        _qv_arrow.arrow_count = max(0, _qv_arrow.arrow_count - 1)

            def _queue_arrow_event(eid: int, entry: dict, n: int = 1) -> None:
                """Armazena evento(s) de flecha: FLT + som diferido para _on_hit."""
                lst = self._player_proj_system.pending_arrow_impacts.setdefault(eid, [])
                for _ in range(n):
                    lst.append(dict(entry))

            if _is_archer_arrow and local_eid is not None:
                _n_arrows = 1  # cada flecha tem seu próprio evento (online: 1 por PROJECTILE_HIT_CS)

            if pos and damage > 0:
                # LOG: jogador local causou dano (imediato — confirmação do servidor)
                if server_attacker == self._my_eid and not _is_dot_hot:
                    from ui.combat_log import LOG as _LOG_cr
                    _suffix_cr = " (crítico)" if is_crit else ""
                    _col_cr    = (255, 220, 50) if is_ability else (220, 220, 220)
                    _LOG_cr.add(f"Você causou {damage} de dano{_suffix_cr}.", _col_cr)
                    if source == "auto":
                        self._player_input_system._increment_pnq_counter(
                            self.player_entity, hit_landed=True)
                if _is_archer_arrow and local_eid is not None:
                    # FLT e som de impacto diferidos — exibidos em _on_hit na colisão visual.
                    # Primeira entrada carrega server_eid/hp_after para atualizar barra de HP
                    # no momento do impacto. Entradas extras (multi-flecha) sem esses campos.
                    _per_dmg = damage // _n_arrows if _n_arrows > 1 else damage
                    _meta_ar = self._meta_from_local(local_eid)
                    _hp_mx_now = _meta_ar.hp_max if _meta_ar else hp_after
                    _queue_arrow_event(local_eid, {
                        "outcome":    outcome,
                        "damage":     _per_dmg,
                        "is_ability": is_ability,
                        "server_eid": server_target,
                        "hp_after":   hp_after,
                        "hp_max":     _hp_mx_now,
                    }, 1)
                    if _n_arrows > 1:
                        _queue_arrow_event(local_eid, {
                            "outcome":    outcome,
                            "damage":     _per_dmg,
                            "is_ability": is_ability,
                        }, _n_arrows - 1)
                    # Cache: server_eid → (local_eid, pos) para is_completion usar
                    # mesmo que o mob despawne antes da mensagem de is_completion chegar.
                    if _sid_cr == "flecha_reiterada":
                        _fr_pos_cache = self.world.get_component(local_eid, Position)
                        self._fr_pending_target[server_target] = (
                            local_eid,
                            _fr_pos_cache.x if _fr_pos_cache else 0.0,
                            _fr_pos_cache.y if _fr_pos_cache else 0.0,
                        )
                    # Sons de reação do mob ficam imediatos (não são da flecha)
                    if not _is_dot_hot:
                        if is_crit:
                            SOUNDS.play_mob_sounds_at(_mob_snd, "crit",
                                                      pos.x, pos.y, _lx, _ly, base=0.7,
                                                      dedup_key=f"crit_{server_target}")
                            SOUNDS.play_emote_at(False, _mob_snd, pos.x, pos.y, _lx, _ly,
                                                 is_crit=True, base=0.7)
                        else:
                            SOUNDS.play_mob_sounds_at(_mob_snd, "emote_attack",
                                                      pos.x, pos.y, _lx, _ly, base=0.6,
                                                      dedup_key=f"dmg_{server_target}")
                else:
                    if is_crit:
                        color = (255, 220, 50) if is_ability else (255, 255, 255)
                        FLT.add(str(damage), pos.x, pos.y, color,
                                is_crit=True, target_id=local_eid)
                        if not _is_dot_hot:
                            SOUNDS.play_random_at(["hit_crit_1", "hit_crit_2", "hit_crit"],
                                                  pos.x, pos.y, _lx, _ly, base=0.8)
                            SOUNDS.play_mob_sounds_at(_mob_snd, "crit",
                                                      pos.x, pos.y, _lx, _ly, base=0.7,
                                                      dedup_key=f"crit_{server_target}")
                            SOUNDS.play_emote_at(False, _mob_snd, pos.x, pos.y, _lx, _ly,
                                                 is_crit=True, base=0.7)
                    else:
                        color = (255, 220, 0) if is_ability else (220, 220, 220)
                        FLT.add(str(damage), pos.x, pos.y, color,
                                "normal", target_id=local_eid)
                        if not _is_dot_hot:
                            if not is_ability:
                                SOUNDS.play_random_at(["hit_normal_1", "hit_normal_2",
                                                       "hit_normal_3", "hit_normal"],
                                                      pos.x, pos.y, _lx, _ly, base=0.6)
                            SOUNDS.play_mob_sounds_at(_mob_snd, "emote_attack",
                                                      pos.x, pos.y, _lx, _ly, base=0.6,
                                                      dedup_key=f"dmg_{server_target}")
            elif pos and damage == 0 and outcome in ("miss", "dodge", "parry", "block", "evade"):
                _AVOID_LABELS = {
                    "miss":  "Errou!",
                    "dodge": "Desviou!",
                    "parry": "Aparou!",
                    "block": "Bloqueou!",
                    # Modo evasão (RETURNING) — outcome vindo de
                    # _server_apply_ranged_physical (ver ARQUITETURA_ONLINE.md).
                    "evade": "Evadiu!",
                }
                if _is_archer_arrow and local_eid is not None:
                    # Texto de esquiva/erro também diferido para colisão visual
                    _queue_arrow_event(local_eid, {
                        "outcome":    outcome,
                        "damage":     0,
                        "is_ability": is_ability,
                    }, _n_arrows)
                else:
                    _col_av = (255, 220, 0) if is_ability else (220, 220, 220)
                    txt_av  = _AVOID_LABELS.get(outcome, "Errou!")
                    FLT.add(txt_av, pos.x, pos.y, _col_av, "small", target_id=local_eid)
                    SOUNDS.play_random_at([f"combat_{outcome}", f"combat_{outcome}_1",
                                           f"combat_{outcome}_2"],
                                          pos.x, pos.y, _lx, _ly, base=0.6)
            return

        # ── Player local foi atacado ou regenerou ─────────────────────
        if server_target == self._my_eid:
            cs = self.world.get_component(self.player_entity, CombatStats)
            if cs and hp_after >= 0:
                if is_regen:
                    cs.current_hp = max(cs.current_hp, hp_after)
                else:
                    cs.current_hp = hp_after
            # Entrar em combate ao receber dano (PvP ou mob)
            if damage > 0:
                from engine.components import CombatState as _CStPvp
                from engine.stat_fns import enter_combat as _ec_pvp_client
                _cst_pvp = self.world.get_component(self.player_entity, _CStPvp)
                if _cst_pvp:
                    _ec_pvp_client(_cst_pvp)
            # Auto-attack de arqueiro (PvP, local ou remoto): flecha visual nasce
            # aqui e o dano/som ficam diferidos para o impacto em _on_hit — inclusive
            # quando erra/desvia (outcome miss/dodge/parry/block, damage=0), igual ao
            # comportamento contra mobs: a flecha voa e erra, sem som de impacto.
            if not is_regen and (damage > 0 or outcome in ("miss", "dodge", "parry", "block")):
                _is_arrow_pl, _atk_eid_pl, _is_self_atk_pl = \
                    self._resolve_archer_attack(cr, server_attacker, source)
                if _is_arrow_pl and source == "auto":
                    player_pos = self.world.get_component(self.player_entity, Position)
                    if player_pos:
                        self._spawn_archer_auto_arrow(_atk_eid_pl, _is_self_atk_pl,
                                                       self.player_entity, player_pos, _lx, _ly)
                        self._player_proj_system.pending_arrow_impacts.setdefault(
                            self.player_entity, []).append({
                                "outcome": outcome, "damage": damage,
                                "is_ability": is_ability, "is_player_target": True,
                            })
                        if damage > 0 and not _is_dot_hot:
                            from ui.combat_log import LOG as _LOG_arrow_pl
                            _suffix_arrow_pl = " (crítico)" if is_crit else ""
                            _LOG_arrow_pl.add(f"Você recebeu {damage} de dano{_suffix_arrow_pl}.",
                                              (220, 80, 80))
                        return
            if is_regen:
                healed = abs(damage)
                if healed > 0:
                    player_pos = self.world.get_component(self.player_entity, Position)
                    if player_pos:
                        FLT.add(f"+{healed} HP", player_pos.x, player_pos.y,
                                col_regen, "normal", target_id=self.player_entity)
            elif damage > 0:
                player_pos = self.world.get_component(self.player_entity, Position)
                if player_pos:
                    FLT.add(f"-{damage}", player_pos.x, player_pos.y,
                            (220, 80, 80), target_id=self.player_entity, is_crit=is_crit)
                # LOG: jogador recebeu dano
                if not _is_dot_hot:
                    from ui.combat_log import LOG as _LOG_cr2
                    _suffix_rcv = " (crítico)" if is_crit else ""
                    _LOG_cr2.add(f"Você recebeu {damage} de dano{_suffix_rcv}.", (220, 80, 80))
                # Som do atacante: skills de flecha (picada_escorpiao, flecha_reiterada,
                # tiro_repulsivo) usam som de impacto de flecha, igual ao auto-attack
                # e ao impacto contra mobs — não o genérico de melee. Se for um mob
                # remoto, toca o som de ataque dele (attack_melee/ranged/magic via
                # MobSounds). Caso contrário (PvP melee/magic), som genérico local.
                if not _is_dot_hot:
                    if cr.get("sid", "") in self._ARROW_SKILL_IDS:
                        SOUNDS.play_random(["arrow_impact_1", "arrow_impact_2"],
                                          channel_group=(12, 13))
                    elif not self._play_attacker_mob_sound(server_attacker, _lx, _ly):
                        if is_crit:
                            SOUNDS.play_emote_get_crit(is_player=True)
                            SOUNDS.play_random(["hit_crit_1","hit_crit_2","hit_crit"], 0.9)
                        elif not is_ability:
                            # Só auto-attack toca hit_normal; som de skill chega via SKILL_EFFECT
                            SOUNDS.play_random(["hit_normal_1","hit_normal_2",
                                                "hit_normal_3","hit_normal"], 0.7)
            elif damage == 0 and outcome in ("miss", "dodge", "parry", "block"):
                # Mob atacou o player mas foi evitado — mostra feedback visual/sonoro
                _AVOID_PLR = {
                    "miss":  ("Errou!",    (220, 220, 100)),
                    "dodge": ("Desviou!",  (100, 210, 230)),
                    "parry": ("Aparou!",   (100, 150, 230)),
                    "block": ("Bloqueou!", (100, 150, 230)),
                }
                _txt_av, _col_av = _AVOID_PLR.get(outcome, ("Errou!", (220, 220, 100)))
                player_pos = self.world.get_component(self.player_entity, Position)
                if player_pos:
                    FLT.add(_txt_av, player_pos.x, player_pos.y, _col_av, "small",
                            target_id=self.player_entity)
                SOUNDS.play_random([f"combat_{outcome}", f"combat_{outcome}_1",
                                    f"combat_{outcome}_2"], 0.7)
            return

        # ── Player remoto foi atacado ou regenerou ────────────────────
        if server_target in self._remote_players:
            local_eid = self._remote_players[server_target]
            rc = self.world.get_component(local_eid, RemoteControlled)
            if rc and hp_after >= 0:
                rc.hp = hp_after
                _hp_max_cr = cr.get("hp_max", 0)
                if _hp_max_cr > 0:
                    rc.hp_max = _hp_max_cr
            pos = self.world.get_component(local_eid, Position)
            # Auto-attack de arqueiro (PvP, contra player remoto): flecha visual
            # nasce aqui; dano/som ficam diferidos para o impacto em _on_hit — inclusive
            # quando erra/desvia (outcome miss/dodge/parry/block, damage=0), igual ao
            # comportamento contra mobs: a flecha voa e erra, sem som de impacto.
            if not is_regen and pos and (damage > 0 or outcome in ("miss", "dodge", "parry", "block")):
                _is_arrow_rp, _atk_eid_rp, _is_self_atk_rp = \
                    self._resolve_archer_attack(cr, server_attacker, source)
                if _is_arrow_rp and source == "auto":
                    self._spawn_archer_auto_arrow(_atk_eid_rp, _is_self_atk_rp,
                                                   local_eid, pos, _lx, _ly)
                    self._player_proj_system.pending_arrow_impacts.setdefault(
                        local_eid, []).append({
                            "outcome": outcome, "damage": damage,
                            "is_ability": is_ability, "is_player_target": True,
                        })
                    return
            if is_regen:
                healed = abs(damage)
                if healed > 0 and pos:
                    FLT.add(f"+{healed} HP", pos.x, pos.y, col_regen, "normal", target_id=local_eid)
            elif damage > 0 and pos:
                FLT.add(f"-{damage}", pos.x, pos.y, (220, 80, 80), target_id=local_eid, is_crit=is_crit)
                if not _is_dot_hot:
                    # Skills de flecha (picada_escorpiao etc.) usam som de impacto de
                    # flecha. Mob remoto atacante → som de ataque dele (attack_melee/
                    # ranged/magic, ex.: "bite"). Caso contrário, genérico (PvP).
                    if cr.get("sid", "") in self._ARROW_SKILL_IDS:
                        SOUNDS.play_random_at(["arrow_impact_1", "arrow_impact_2"],
                                              pos.x, pos.y, _lx, _ly, base=1.0, channel_group=(12, 13))
                    elif not self._play_attacker_mob_sound(server_attacker, _lx, _ly):
                        if is_crit:
                            SOUNDS.play_random_at(["hit_crit_1","hit_crit_2","hit_crit"],
                                                  pos.x, pos.y, _lx, _ly, base=0.7)
                        elif not is_ability:
                            # Só auto-attack toca hit_normal; som de skill já tocou em is_completion
                            SOUNDS.play_random_at(["hit_normal_1","hit_normal_2",
                                                   "hit_normal_3","hit_normal"],
                                                  pos.x, pos.y, _lx, _ly, base=0.6)
            elif damage == 0 and pos and outcome in ("miss", "dodge", "parry", "block"):
                _AVOID_RP = {
                    "miss":  ("Errou!",    (220, 220, 100)),
                    "dodge": ("Desviou!",  (100, 210, 230)),
                    "parry": ("Aparou!",   (100, 150, 230)),
                    "block": ("Bloqueou!", (100, 150, 230)),
                }
                _txt_rp, _col_rp = _AVOID_RP.get(outcome, ("Errou!", (220, 220, 100)))
                FLT.add(_txt_rp, pos.x, pos.y, _col_rp, "small", target_id=local_eid)
                if not _is_dot_hot:
                    SOUNDS.play_random_at([f"combat_{outcome}", f"combat_{outcome}_1",
                                          f"combat_{outcome}_2"],
                                          pos.x, pos.y, _lx, _ly, base=0.6)

    def _sync_player_effects(self, effects: list) -> None:
        """Sincroniza StatusEffects do jogador local com o estado autoritativo do servidor.

        O servidor envia a lista atual de efeitos ativos (type + duration restante).
        O cliente aplica localmente SEM dano (tick_interval=0) — apenas para:
          - Exibir ícones de efeito no HUD
          - Aplicar slow/root/stun ao movimento e ações do cliente
        O dano real vem separado via COMBAT_RESULT (source = effect_type).
        """
        from engine.components import StatusEffects, ActiveEffect
        from content.status_effects_data import EFFECT_DEFS as _EDEFS
        sfx = self.world.get_component(self.player_entity, StatusEffects)
        if sfx is None:
            sfx = StatusEffects()
            self.world.add_component(self.player_entity, sfx)

        server_types = {e["type"] for e in effects}

        # Remove efeitos expirados no servidor — LOG de expiração
        for k in list(sfx.effects.keys()):
            if k not in server_types:
                _defn = _EDEFS.get(k)
                _label = _defn.label if _defn else k
                LOG.add(f"{_label} expirou.", (160, 160, 160))
                sfx.effects.pop(k, None)

        # Aplica / atualiza efeitos do servidor — LOG quando efeito é novo
        for e in effects:
            etype = e["type"]
            dur   = float(e.get("duration", 1.0))
            if etype not in sfx.effects:
                # Efeito novo: adiciona e loga
                _defn = _EDEFS.get(etype)
                _label = _defn.label if _defn else etype
                if _defn and _defn.is_buff:
                    LOG.add(f"{_label} ativado!", (100, 220, 120))
                else:
                    LOG.add(f"Você recebeu: {_label}!", (220, 100, 60))
                sfx.effects[etype] = ActiveEffect(
                    effect_type=etype,
                    duration=dur,
                    magnitude=0.0,      # sem dano local — só servidor aplica dano
                    tick_interval=0.0,  # sem tick local de dano
                )
            else:
                sfx.effects[etype].duration = dur

        # Recalcula slow_mult/is_rooted/is_crowd_controlled a partir do
        # StatusEffects atual — sem isso, um efeito removido acima (pop
        # direto, fora do loop de StatusEffectSystem.update()) deixava
        # esses 3 estados derivados PRESOS no último valor pra sempre
        # (bug real relatado pelo usuário 20/07/2026: alvo de Polimorfia
        # ficava permanentemente lento depois do efeito expirar). Ver
        # engine/core_systems.py::sync_status_derived_state.
        from engine.core_systems import sync_status_derived_state
        sync_status_derived_state(self.world, self.player_entity, sfx)

    def _sync_mob_effects(self, mob_effects: dict) -> None:
        """Sincroniza efeitos de status em mobs remotos.

        mob_effects: {str(server_eid): [{type, duration}, ...]}
        Aplica o componente StatusEffects nas entidades locais dos mobs.
        sem tick de dano — apenas para renderização (ícones acima da barra de HP)
        e LOG de novos efeitos de controle.
        """
        from engine.components import StatusEffects, ActiveEffect
        from content.status_effects_data import EFFECT_DEFS as _EDEFS

        from engine.core_systems import sync_status_derived_state

        # Limpa efeitos de mobs que o servidor não enviou neste tick
        _reported_server_eids = {int(k) for k in mob_effects}
        for srv_eid, local_eid in self._remote_mobs.items():
            if srv_eid not in _reported_server_eids:
                sfx = self.world.get_component(local_eid, StatusEffects)
                if sfx and sfx.effects:
                    sfx.effects.clear()
                    # Sem isso, slow_mult/is_rooted/is_crowd_controlled do
                    # mob ficavam presos no último valor pra sempre — ver
                    # comentário completo em _sync_player_effects acima.
                    sync_status_derived_state(self.world, local_eid, sfx)

        for srv_eid_str, effects in mob_effects.items():
            srv_eid   = int(srv_eid_str)
            # Busca mob remoto OU player remoto (PvP) como entidade local
            local_eid = self._remote_mobs.get(srv_eid)
            if local_eid is None:
                local_eid = self._remote_players.get(srv_eid)
            if local_eid is None:
                continue

            sfx = self.world.get_component(local_eid, StatusEffects)
            if sfx is None:
                sfx = StatusEffects()
                self.world.add_component(local_eid, sfx)

            server_types = {e["type"] for e in effects}

            # Remove expirados
            for k in list(sfx.effects.keys()):
                if k not in server_types:
                    sfx.effects.pop(k, None)

            # Aplica / atualiza — LOG para novos efeitos de CC
            for e in effects:
                etype = e["type"]
                dur   = float(e.get("duration", 1.0))
                if etype not in sfx.effects:
                    _defn = _EDEFS.get(etype)
                    if _defn and not _defn.is_buff:
                        _label = _defn.label
                        from engine.components import EntityIdentity as _EIdentMob
                        _ident = self.world.get_component(local_eid, _EIdentMob)
                        _mname = _ident.name if _ident else "Alvo"
                        LOG.add(f"{_mname}: {_label}!", (255, 180, 80))
                    sfx.effects[etype] = ActiveEffect(
                        effect_type=etype,
                        duration=dur,
                        magnitude=0.0,
                        tick_interval=0.0,
                    )
                else:
                    sfx.effects[etype].duration = dur

            sync_status_derived_state(self.world, local_eid, sfx)

    def _sync_combat_target(self) -> None:
        """Envia AUTO_ATTACK ao servidor.

        Offline: clique esquerdo = seleciona (is_pursuing=False), não ataca.
                 clique direito  = seleciona + persegue (is_pursuing=True), ataca.
        Online:  só envia AUTO_ATTACK quando is_pursuing=True — igual ao offline.
        Grace period: após enviar AUTO_ATTACK com alvo válido, aguarda 3 frames antes
        de enviar -1, evitando que glitches de 1-2 frames de is_pursuing parem o ataque.
        """
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from engine.components import CombatState
        cs = self.world.get_component(self.player_entity, CombatState)
        if not cs:
            return
        local_target = cs.target_entity_id
        pursuing_target = local_target if cs.is_pursuing else -1
        server_target = -1
        if pursuing_target != -1:
            # Primeiro tenta mob remoto via RemoteEntityMeta; depois player remoto (PvP)
            _meta_st = self._meta_from_local(pursuing_target)
            server_target = _meta_st.server_eid if _meta_st else -1
            if server_target == -1:
                from engine.components import RemoteControlled as _RCsync
                _rc_sync = self.world.get_component(pursuing_target, _RCsync)
                if _rc_sync is not None:
                    server_target = _rc_sync.server_eid

        # Grace period: se enviamos um alvo válido recentemente, não envia -1 imediatamente
        # Isso evita que glitches de is_pursuing por 1-2 frames parem o ataque
        if not hasattr(self, '_sync_grace'):
            self._sync_grace = 0
        if server_target != -1:
            self._sync_grace = 3   # 3 frames de grace após alvo válido
        elif self._sync_grace > 0:
            self._sync_grace -= 1
            return  # ainda no grace period — não envia -1

        if server_target != self._net_last_target:
            self._net.send(
                __import__("shared.messages", fromlist=["MsgType"]).MsgType.AUTO_ATTACK,
                {"tid": server_target})
            self._net_last_target = server_target

    def _spawn_remote_mob(self, server_eid: int, data: dict) -> None:
        """Cria mob no ECS local a partir de dados do servidor."""
        if server_eid in self._remote_mobs:
            from debug.aoi_debug import AOI_DBG
            _existing_local = self._remote_mobs[server_eid]
            AOI_DBG.log("SPAWN_SKIP", server_eid=server_eid,
                        local_eid=_existing_local,
                        entity_alive=(self.world.get_component(_existing_local, Position) is not None))
            return

        from engine.entity_factory import create_enemy
        from engine.components import Renderable
        local_eid = create_enemy(
            self.world,
            data.get("tx", 0),
            data.get("ty", 0),
            attack_range = 3 if data.get("is_ranged", False) else 1,
            is_ranged    = data.get("is_ranged", False),
            tier         = data.get("tier", "normal"),
            race         = data.get("race", "Humanoide"),
            entity_class = data.get("entity_class", ""),
            level        = data.get("level", 1),
            # Facção real do servidor (content/faction_data.py) — usada só
            # pra colorir a barra de HP do nameplate por disposição
            # (vermelho/amarelo claro/verde, ver _draw_mob_hp_bars). O
            # componente Faction que create_enemy() já anexa ficava sempre
            # no default (monstros_hostis) sem isso.
            faction      = data.get("faction", "monstros_hostis"),
        )
        # Aplica cor do servidor
        server_color = data.get("color")
        if server_color:
            ren = self.world.get_component(local_eid, Renderable)
            if ren:
                ren.color = tuple(server_color)

        # Nome próprio do servidor (ex: "Boneco de treino") — create_enemy()
        # deriva o nome exibido a partir da raça por padrão (mob_display_name
        # = race), errado pra entidades com identidade própria sem zona de
        # spawn (ex: boneco de treino, antes aparecia como "Humanoide").
        _server_name = data.get("name")
        if _server_name:
            from engine.components import EntityIdentity as _EIdSpawn
            _eid_ident = self.world.get_component(local_eid, _EIdSpawn)
            if _eid_ident:
                _eid_ident.name = _server_name

        # is_dummy: marca o proxy local como boneco de treino — sem isso, skills
        # usadas contra ele nunca contavam pra objetivos de quest com
        # params={"on_dummy": True} (ver quest_system.py / systems.py::_use_skill*),
        # já que o cliente não tinha nenhum jeito de saber que era um boneco.
        if data.get("is_dummy"):
            from engine.components import TrainingDummy as _TDSpawn
            self.world.add_component(local_eid, _TDSpawn())

        # NPC de serviço (mercador/ferreiro/treinador/dador-de-missão, Fase 1
        # de combate genérico, 21/07/2026) — anexa o(s) mesmo(s) componente(s)
        # de capacidade que o servidor tem, um por campo condicional presente
        # no payload (ver server/world_server.py::_build_mob_spawn_payload).
        # ShopSystem/TrainerSystem/QuestDialogSystem (ui/systems.py,
        # ui/trainer_system.py, ui/quest_system.py) já são 100% agnósticas
        # de como a entidade foi criada — só olham
        # Position+Renderable+Merchant/Trainer/QuestGiver/Blacksmith — então
        # loja/treino/missão passam a funcionar na entidade remota sem
        # NENHUMA mudança nesses sistemas.
        _profession = data.get("profession")
        if _profession is not None:
            from engine.components import NPC as _NPCSpawn
            self.world.add_component(local_eid, _NPCSpawn(
                name=_server_name or data.get("race", "NPC"),
                level=data.get("level", 1), profession=_profession))
        _shop_id = data.get("shop_id")
        if _shop_id is not None:
            from engine.components import Merchant as _MerchSpawn
            self.world.add_component(local_eid, _MerchSpawn(shop_id=_shop_id))
        if data.get("is_blacksmith"):
            from engine.components import Blacksmith as _BlackSpawn
            self.world.add_component(local_eid, _BlackSpawn(shop_id=_shop_id or "blacksmith"))
        _class_id = data.get("class_id")
        if _class_id is not None:
            from engine.components import Trainer as _TrainSpawn
            self.world.add_component(local_eid, _TrainSpawn(class_id=_class_id))
        if data.get("quest_ids") is not None:
            from engine.components import QuestGiver as _QGSpawn
            self.world.add_component(local_eid, _QGSpawn(
                quest_ids=tuple(data.get("quest_ids", [])),
                turn_in_ids=tuple(data.get("turn_in_ids", []))))

        # Remove CombatStats: HP é autoritativo pelo servidor (RemoteEntityMeta.hp).
        # Remove AIControlled: mobs remotos são movidos por ENTITY_MOVE do servidor;
        # sem isso, EnemyAISystem local emite start_tile_movement competindo com o servidor,
        # causando snapback de 1 tile quando os alvos divergem.
        from engine.components import CombatStats, AIControlled as _AIC_rm
        self.world.remove_component(local_eid, CombatStats)
        if self.world.get_component(local_eid, _AIC_rm) is not None:
            self.world.remove_component(local_eid, _AIC_rm)

        # Inicializa RemoteEntityMeta — substitui _mob_hp, _mob_last_pos, _remote_mobs_reverse
        hp_max = data.get("hp_max", 100)
        hp     = data.get("hp",     hp_max)
        from engine.components import RemoteEntityMeta as _REM, Position as _PosLp
        _pos_lp = self.world.get_component(local_eid, _PosLp)
        _meta = _REM(
            server_eid = server_eid,
            hp         = hp,
            hp_max     = hp_max,
            last_x     = _pos_lp.x if _pos_lp else 0.0,
            last_y     = _pos_lp.y if _pos_lp else 0.0,
        )
        self.world.add_component(local_eid, _meta)
        self._remote_mobs[server_eid] = local_eid
        from debug.aoi_debug import AOI_DBG
        AOI_DBG.log("SPAWN_OK", server_eid=server_eid, local_eid=local_eid,
                    tx=data.get("tx"), ty=data.get("ty"),
                    race=data.get("race"), entity_class=data.get("entity_class"))

        # Inicializa server_tile com o spawn tile (tile autoritativo do servidor)
        from engine.components import TileMovement as _TMInit
        _tm_init = self.world.get_component(local_eid, _TMInit)
        if _tm_init:
            _tm_init.server_tile_x = data.get("tx", 0)
            _tm_init.server_tile_y = data.get("ty", 0)

        # Se o mob já estava em movimento no servidor no momento do spawn,
        # inicia a animação imediatamente (evita pop-in estático + teleporte).
        mtx = data.get("moving_to_tx")
        mty = data.get("moving_to_ty")
        if mtx is not None and mty is not None:
            from engine.components import TileMovement as _TMSpawn, Position as _PosSpawn
            from engine.utils import start_tile_movement as _stm
            _tm_sp  = self.world.get_component(local_eid, _TMSpawn)
            _pos_sp = self.world.get_component(local_eid, _PosSpawn)
            if _tm_sp and _pos_sp:
                _stm(_pos_sp, _tm_sp, mtx, mty)

    def _spawn_mob_projectile(self, server_proj_eid: int, data: dict) -> None:
        """Cria entidade visual de projétil de mob para o cliente renderizar.

        O servidor já processa dano — aqui é só cosmético.
        Reutiliza o ProjectileSystem local (já em self.systems) para mover e remover.
        target_id = self.player_entity se o alvo for o player local.
        """
        from engine.components import Position as _PP, Projectile as _ProjC
        # Posição inicial enviada pelo servidor (pixel)
        px = float(data.get("x", 0))
        py = float(data.get("y", 0))

        # Alvo: servidor envia eid ECS do alvo. Se for o player local, usa player_entity.
        # Para outros players, ignora por ora (sem entidade local mapeada aqui).
        target_seid = data.get("target_seid", -1)
        if target_seid == self._my_eid:
            target_local = self.player_entity
        elif target_seid in self._remote_players:
            target_local = self._remote_players[target_seid]
        else:
            return  # alvo não visível localmente

        color    = tuple(data.get("color",    (220, 160, 60)))
        is_arrow = bool(data.get("is_arrow",  True))
        dir_x    = float(data.get("dir_x",    1.0))
        dir_y    = float(data.get("dir_y",    0.0))
        speed    = float(data.get("speed",    380.0))

        local_eid = self.world.create_entity()
        self.world.add_component(local_eid, _PP(x=px, y=py, prev_x=px, prev_y=py))
        self.world.add_component(local_eid, _ProjC(
            attacker_id  = -1,          # dano já processado no servidor
            target_id    = target_local,
            damage_type  = "physical",  # nunca dispara deal_damage (attacker=-1 → sem CombatStats)
            speed        = speed,
            color        = color,
            is_arrow     = is_arrow,
            dir_x        = dir_x,
            dir_y        = dir_y,
        ))
        # Mapeia server_eid → local para que ENTITY_DESPAWN possa remover
        self._remote_mob_projectiles[server_proj_eid] = local_eid

    def _move_remote_mob(self, server_eid: int, new_tx: int, new_ty: int,
                         from_tx: int | None = None, from_ty: int | None = None,
                         is_dash: bool = False, duration: float | None = None) -> None:
        """Move mob remoto para o tile destino recebido do servidor.

        O servidor envia target_tile quando o movimento COMEÇA (não quando termina),
        então cliente e servidor animam em paralelo.

        from_tx/from_ty: tile onde o mob ESTÁ no servidor quando este passo começa.
        Gravado em tm.server_tile_x/y para que _process_target use o mesmo critério
        de distância que o servidor (em vez da posição visual animada, que fica atrás).

        duration: presente apenas em DESLOCAMENTOS FORÇADOS de múltiplos tiles em
        UM evento só (ex: knockback do Tiro Repulsivo) — servidor já decidiu a
        posição final e quanto tempo a tween deve levar; cliente não enfileira
        nem prediz, só anima a tween inteira de uma vez (padrão de netcode pra
        displacement: servidor autoritativo, cliente só interpola o confirmado).
        Isso PREEMPTA qualquer animação/fila em andamento — o deslocamento forçado
        tem prioridade sobre o que o mob estava fazendo.
        """
        from engine.components import TileMovement, Position
        from engine.utils import start_tile_movement
        local_eid = self._remote_mobs.get(server_eid)
        if local_eid is None:
            return
        tm  = self.world.get_component(local_eid, TileMovement)
        pos = self.world.get_component(local_eid, Position)
        if not tm or not pos:
            return

        # Atualiza tile autoritativo do servidor (destino = onde mob ESTÁ no servidor agora).
        # O servidor move mobs instantaneamente; new_tx/ty é a posição real atual.
        # from_tx/ty seria a posição anterior — menos útil para dist_attack.
        tm.server_tile_x = new_tx
        tm.server_tile_y = new_ty
        # Mantém last_x/y no componente para leituras ECS enquanto mob vive
        from engine.components import RemoteEntityMeta as _REM_mv
        _meta_mv = self.world.get_component(local_eid, _REM_mv)
        if _meta_mv:
            _meta_mv.last_x = new_tx * TILE_SIZE + TILE_SIZE // 2
            _meta_mv.last_y = new_ty * TILE_SIZE + TILE_SIZE // 2

        if duration is not None:
            # Deslocamento forçado: descarta qualquer fila/animação em curso e
            # tween direto, AGORA, do tile atual visual até o destino — uma
            # transição só, não N passos. Evita por completo a classe de bugs
            # de fila (tile pulado por dedup, descompasso com perseguição que
            # já começou, etc), já que não existe mais fila para esse evento.
            self._mob_move_queues.pop(server_eid, None)
            start_tile_movement(pos, tm, new_tx, new_ty, override_duration=duration)
            tm.is_dash = duration > 0
            return

        if tm.is_moving:
            # Já animando para este tile A PARTIR DO MESMO PONTO DE PARTIDA? Não
            # enfileira (servidor emite start, não end — evita reprocessar o mesmo
            # evento 2x). Compara from_tx/ty também: só o destino não basta — um
            # caminho de volta que passa pelo MESMO tile de destino do passo de
            # saída batia o destino por coincidência e era descartado como
            # "duplicata", pulando esse tile inteiro.
            _same_origin = (from_tx is None or
                            (tm.current_tile_x == from_tx and tm.current_tile_y == from_ty))
            if tm.target_tile_x == new_tx and tm.target_tile_y == new_ty and _same_origin:
                return
            # Indo para outro tile — enfileira o próximo passo
            queue = self._mob_move_queues.setdefault(server_eid, [])
            # Descarta entrada duplicada no topo da fila
            if not queue or queue[-1][:2] != (new_tx, new_ty):
                queue.append((new_tx, new_ty, is_dash))
        else:
            start_tile_movement(pos, tm, new_tx, new_ty)
            if is_dash:
                tm.is_dash       = True
                tm.move_duration = 0.18

    def _ensure_remote_mobs_visible(self) -> None:
        """
        Garante que mobs e players remotos sempre tenham Visible após FogSystem rodar.
        O servidor decidiu que o cliente deve ver essas entidades (estão no AOI e
        passaram por _can_see) — fog of war/LOS é mecânica local de exploração, não
        deve sobrepor essa decisão. Sem isso, FogSystem remove Visible quando há
        paredes no caminho do LOS, causando PlayerInputSystem limpar o target
        selecionado a cada frame (mob OU player remoto em PvP).
        """
        from engine.components import Visible
        for local_eid in self._remote_mobs.values():
            if self.world.get_component(local_eid, Visible) is None:
                self.world.add_component(local_eid, Visible())
        for local_eid in self._remote_players.values():
            if self.world.get_component(local_eid, Visible) is None:
                self.world.add_component(local_eid, Visible())

    # ── Helpers de acesso a RemoteEntityMeta ─────────────────────────────────

    # Segundos — segurança se a animação travar. Cobre cadeias longas (ex: knockback
    # de 5 tiles a 0.18s/tile ≈ 0.9s) com margem, sem deixar o corpse pendurado pra sempre.
    _PENDING_DESPAWN_TIMEOUT = 2.0

    def _flush_pending_mob_despawns(self, dt: float) -> None:
        """Remove entidades de mob cujo despawn foi adiado até a animação de movimento concluir."""
        if not self._pending_mob_despawn:
            return
        from engine.components import TileMovement as _TM_pd
        done = []
        for local_eid, info in self._pending_mob_despawn.items():
            info["timer"] += dt
            tm = self.world.get_component(local_eid, _TM_pd)
            _queue_pending = bool(self._mob_move_queues.get(info["server_eid"]))
            if (tm is None or (not tm.is_moving and not _queue_pending)
                    or info["timer"] >= self._PENDING_DESPAWN_TIMEOUT):
                done.append(local_eid)
        for local_eid in done:
            info = self._pending_mob_despawn.pop(local_eid)
            self._mob_move_queues.pop(info["server_eid"], None)
            try:
                self.world.remove_entity(local_eid)
            except Exception:
                pass

    def _meta(self, server_eid: int):
        """Retorna RemoteEntityMeta do mob remoto pelo server_eid, ou None."""
        local_eid = self._remote_mobs.get(server_eid)
        if local_eid is None:
            return None
        from engine.components import RemoteEntityMeta as _REM
        return self.world.get_component(local_eid, _REM)

    def _meta_from_local(self, local_eid: int):
        """Retorna RemoteEntityMeta do mob remoto pelo local_eid, ou None."""
        from engine.components import RemoteEntityMeta as _REM
        return self.world.get_component(local_eid, _REM)

    # ─────────────────────────────────────────────────────────────────────────

    def _process_mob_move_queues(self) -> None:
        """Processa fila de movimentos de mobs — chamado a cada frame."""
        from engine.components import TileMovement, Position
        from engine.utils import start_tile_movement
        for server_eid, queue in list(self._mob_move_queues.items()):
            if not queue:
                del self._mob_move_queues[server_eid]
                continue
            local_eid = self._remote_mobs.get(server_eid)
            if not local_eid:
                del self._mob_move_queues[server_eid]
                continue
            tm  = self.world.get_component(local_eid, TileMovement)
            pos = self.world.get_component(local_eid, Position)
            if not tm or not pos:
                del self._mob_move_queues[server_eid]
                continue
            if not tm.is_moving:
                tx, ty, is_dash = queue.pop(0)
                start_tile_movement(pos, tm, tx, ty)
                if is_dash:
                    tm.is_dash       = True
                    tm.move_duration = 0.18
                if not queue:
                    del self._mob_move_queues[server_eid]

        # Fila de movimentos de players remotos — mesmo padrão dos mobs
        for server_eid, queue in list(self._remote_player_move_queues.items()):
            if not queue:
                del self._remote_player_move_queues[server_eid]
                continue
            local_eid = self._remote_players.get(server_eid)
            if not local_eid:
                del self._remote_player_move_queues[server_eid]
                continue
            tm  = self.world.get_component(local_eid, TileMovement)
            pos = self.world.get_component(local_eid, Position)
            if not tm or not pos:
                del self._remote_player_move_queues[server_eid]
                continue
            if not tm.is_moving:
                tx, ty, is_dash = queue.pop(0)
                start_tile_movement(pos, tm, tx, ty)
                if is_dash:
                    tm.is_dash       = True
                    tm.move_duration = 0.18
                if not queue:
                    del self._remote_player_move_queues[server_eid]

        # Fila de correções "is_dash" do próprio player (ex: knockback sofrido) —
        # mesmo padrão acima, mas no player_entity local.
        if self._self_move_queue:
            tm  = self.world.get_component(self.player_entity, TileMovement)
            pos = self.world.get_component(self.player_entity, Position)
            if tm and pos and not tm.is_moving:
                tx, ty = self._self_move_queue.pop(0)
                start_tile_movement(pos, tm, tx, ty)
                tm.is_dash       = True
                tm.move_duration = 0.18
                # Suprime o MOVE espúrio que _send_player_move() mandaria por ver
                # target_tile mudar (mesma razão do sync em network_handlers.py).
                self._net_last_tx = tx
                self._net_last_ty = ty

    # ── Jogadores remotos — abordagem ECS ────────────────────────────────────

    def _spawn_remote_player_entity(self, server_eid: int, data: dict) -> None:
        """Cria entidade ECS real para jogador remoto. TileMovementSystem anima."""
        if server_eid in self._remote_players:
            return
        from engine.components import (Position, TileMovement, Renderable,
                                 Visible, RemoteControlled)
        from engine.utils import start_tile_movement
        from engine.tileset import TILE_SIZE as _TS

        tx = data.get("tx", 0)
        ty = data.get("ty", 0)
        px = tx * _TS + _TS // 2
        py = ty * _TS + _TS // 2

        _CLASS_COLORS = {"guerreiro": (200,80,80), "mago": (80,80,220), "arqueiro": (80,200,80)}
        col = _CLASS_COLORS.get(data.get("class_id", "guerreiro"), (180, 180, 180))
        if data.get("kind") == "player_corpse":
            col = tuple(int(c * 0.35) + 20 for c in col)
        elif data.get("is_ghost"):
            # Ghost de outro player: cor semi-transparente (azulada/acinzentada)
            col = tuple(int(c * 0.4) + 60 for c in col)

        local_eid = self.world.create_entity()
        self.world.add_component(local_eid, Position(x=px, y=py, prev_x=px, prev_y=py))
        from engine.entity_factory import PLAYER_SPEED as _PS_remote
        self.world.add_component(local_eid, TileMovement(
            current_tile_x=tx, current_tile_y=ty,
            target_tile_x=tx,  target_tile_y=ty,
            speed=_PS_remote,                      # igual ao player local → move_duration correto
            move_duration=_TS / _PS_remote,        # pre-calcula para o primeiro movimento
        ))
        self.world.add_component(local_eid, Renderable(
            color=col, width=_TS - 4, height=_TS - 4))
        self.world.add_component(local_eid, Visible())
        self.world.add_component(local_eid, RemoteControlled(
            server_eid=server_eid,
            name=data.get("name", "?"),
            class_id=data.get("class_id", "guerreiro"),
            hp=data.get("hp", 100),
            hp_max=data.get("hp_max", 100),
            level=data.get("level", 1),
        ))
        self._remote_players[server_eid] = local_eid

        # Reacquire alvo PvP: se estávamos perseguindo este player antes de ele morrer
        if server_eid == self._pvp_respawn_target:
            from engine.components import CombatState as _CStRe
            _cs_re = self.world.get_component(self.player_entity, _CStRe)
            if _cs_re and _cs_re.is_pursuing:
                _cs_re.target_entity_id = local_eid
            self._pvp_respawn_target = -1  # consumido

    def _apply_remote_move(self, eid: int, new_tx: int, new_ty: int,
                           from_tx: int | None = None, from_ty: int | None = None,
                           is_dash: bool = False, teleport: bool = False,
                           duration: float | None = None) -> None:
        """Atualiza target_tile do jogador remoto — TileMovementSystem anima.

        from_tx/from_ty: posição anterior confirmada pelo servidor.
        Quando disponíveis, garante que a animação parta do tile correto,
        eliminando desyncs acumulados (player parece "pular" entre tiles).

        teleport: posição final é instantânea (ex: respawn pós-morte) — não
        anima a caminhada entre from_tx/from_ty e new_tx/new_ty.
        """
        from engine.components import TileMovement, Position
        from engine.utils import start_tile_movement
        from engine.tileset import TILE_SIZE as _TS_rm
        local_eid = self._remote_players.get(eid)
        if local_eid is None:
            return
        tm  = self.world.get_component(local_eid, TileMovement)
        pos = self.world.get_component(local_eid, Position)
        if not tm or not pos:
            return
        if teleport:
            self._remote_player_move_queues.pop(eid, None)
            tm.is_moving      = False
            tm.is_dash        = False
            tm.progress       = 0.0
            tm.current_tile_x = new_tx
            tm.current_tile_y = new_ty
            tm.target_tile_x  = new_tx
            tm.target_tile_y  = new_ty
            pos.x = new_tx * _TS_rm + _TS_rm / 2
            pos.y = new_ty * _TS_rm + _TS_rm / 2
            return
        if duration is not None:
            # Deslocamento forçado (ex: knockback do Tiro Repulsivo em PvP) — UM
            # evento com posição final + duração, mesmo padrão de _move_remote_mob.
            # Preempta fila/animação em curso: servidor já decidiu tudo, cliente
            # só interpola a tween confirmada, sem enfileirar passo a passo.
            self._remote_player_move_queues.pop(eid, None)
            start_tile_movement(pos, tm, new_tx, new_ty, override_duration=duration)
            tm.is_dash = duration > 0
            return
        if not tm.is_moving:
            # Se temos a posição "de" confirmada, alinha o visual antes de animar.
            # Só aplica quando parado (sem interromper animação em curso).
            if from_tx is not None and from_ty is not None:
                pos.x = from_tx * _TS_rm + _TS_rm / 2
                pos.y = from_ty * _TS_rm + _TS_rm / 2
                tm.current_tile_x = from_tx
                tm.current_tile_y = from_ty
            start_tile_movement(pos, tm, new_tx, new_ty)
            if is_dash:
                tm.is_dash       = True
                tm.move_duration = 0.18
        else:
            # Já animando: encadeia na fila para não interromper a animação atual.
            # Compara from_tx/ty também (não só o destino) — mesma razão do mob:
            # um retorno que passa pelo mesmo tile de destino do passo de saída
            # (ex: knockback + perseguição na mesma linha) batia o destino por
            # coincidência com origem diferente e era descartado como duplicata.
            _same_origin_rm = (from_tx is None or
                               (tm.current_tile_x == from_tx and tm.current_tile_y == from_ty))
            if (tm.target_tile_x == new_tx and tm.target_tile_y == new_ty
                    and _same_origin_rm):
                return  # já está indo para este tile, a partir da mesma origem
            queue = self._remote_player_move_queues.setdefault(eid, [])
            entry = (new_tx, new_ty, is_dash)
            if not queue or queue[-1][:2] != (new_tx, new_ty):
                queue.append(entry)
        # Passo do player remoto — atenuado por distância, throttle por timer
        if not is_dash:
            _step_timer = self._remote_step_timers.get(eid, 0.0)
            if _step_timer <= 0.0:
                _slx, _sly = self._player_world_pos()
                SOUNDS.play_random_at(
                    ["step_1","step_2","step_3","step_4","step_5",
                     "step_6","step_7","step_8","step_9"],
                    pos.x, pos.y, _slx, _sly, base=0.35,
                    channel_group=None,
                )
                self._remote_step_timers[eid] = 0.25  # 250ms entre passos

    def _draw_mob_hp_bars(self, cam_x: float, cam_y: float) -> None:
        """Desenha barras de HP dos mobs remotos com dados autoritativos do servidor."""
        from engine.components import Position, FogOfWar as _FogComp, RemoteEntityMeta as _REM_hb
        if not self._remote_mobs:
            return
        from engine.tileset import TILE_SIZE as _TS
        W = _TS - 4
        zoom_surf = self._zoom_surf
        _fog_vis = None
        for _, _fog in self.world.get_entities_with(_FogComp):
            _fog_vis = _fog.visible
            break
        for local_eid, meta in self.world.get_entities_with(_REM_hb):
            hp, hp_max = meta.hp, meta.hp_max
            pos = self.world.get_component(local_eid, Position)
            if not pos:
                continue
            if _fog_vis is not None:
                etx = int(pos.x / _TS)
                ety = int(pos.y / _TS)
                if (etx, ety) not in _fog_vis:
                    continue
            # Fundo+barra+número do nível saem como UMA ÚNICA Surface (ver
            # ui/hud_bars.py) inteira em WORLD_LABELS (screen-space) —
            # nunca mais divididos entre espaço de mundo e espaço de tela
            # (causava um bug real: número "flutuando" fora da caixinha,
            # ver ARQUITETURA_ONLINE.md 23.9).
            _world_y_top = pos.y - W / 2
            if hp_max > 0:
                from ui.hud_bars import build_mob_hud as _bmh_hb, HUD_GAP_PX as _HGP_hb, effects_row_offset as _ero_hb
                from ui.world_labels import WORLD_LABELS as _WL_hb
                ratio = max(0.0, hp / hp_max)

                from engine.components import EntityIdentity as _EIdHb
                _mob_id_hb = self.world.get_component(local_eid, _EIdHb)
                _level_hb = _mob_id_hb.level if _mob_id_hb is not None else 1

                if not hasattr(self, '_mob_level_font'):
                    from ui.fonts import make as _make_name_hb2
                    from ui.hud_bars import LEVEL_FONT_SIZE as _LFS_hb
                    self._mob_level_font = _make_name_hb2(_LFS_hb * 2)

                # Cor da barra por disposição (hostil/neutro/amigavel) —
                # Faction real do servidor foi anexada em _spawn_remote_mob
                # via create_enemy(faction=...). Pedido do usuário
                # 15/07/2026, depois de testar o Lobo neutro pela 1ª vez.
                from engine.components import Faction as _FacHb
                from content.faction_data import get_relationship as _getrel_hb, PLAYER_FACTION as _PF_hb
                from ui.hud_bars import DISPOSITION_HP_COLORS as _DISPCOL_hb
                _fac_hb = self.world.get_component(local_eid, _FacHb)
                _tier_hb = (_getrel_hb(_fac_hb.faction_id, _PF_hb)
                           if _fac_hb is not None else "hostil")
                _hp_color_hb = _DISPCOL_hb.get(_tier_hb, _DISPCOL_hb["hostil"])

                _hud_surf = _bmh_hb(ratio, _level_hb, self._mob_level_font, hp_color=_hp_color_hb)
                _WL_hb.add_icon(pos.x, _world_y_top, _hud_surf,
                                stack_key=local_eid, gap_before=_HGP_hb)

                # Nome — mesmo padrão do RenderSystem offline (ui/systems.py),
                # espelhado aqui porque mob remoto não passa por aquele loop
                # (renderizado à parte, ver _spawn_remote_mob).
                # EntityIdentity.name/.level já são setados no spawn.
                # self.font_sm (não uma instância própria): MESMO objeto de
                # fonte da janela de chat — pedido explícito do usuário
                # 17/07/2026 ("mesmo estilo do nick que está no chat"). Usar
                # o atributo direto (em vez de cachear numa instância
                # própria) mantém sincronia automática se _ui_scale mudar
                # (self.font_sm é recriado por _reload_ui_fonts()).
                if _mob_id_hb is not None:
                    _WL_hb.add_text(pos.x, _world_y_top,
                                    _mob_id_hb.name, self.font_sm, (220, 200, 180),
                                    stack_key=local_eid, gap_before=2)

                # Ícones de efeito à DIREITA da HUD (pedido do usuário 11/07/2026)
                from engine.components import StatusEffects as _SfxDraw
                _sfx = self.world.get_component(local_eid, _SfxDraw)
                _active_effects = list(_sfx.effects.values()) if _sfx else []
                if _active_effects:
                    from ui.systems import _build_effects_row as _ber_hb
                    if not hasattr(self, '_mob_eff_font'):
                        import pygame as _pg
                        self._mob_eff_font = _pg.font.Font(None, 18)
                    _row = _ber_hb(_active_effects, self._mob_eff_font)
                    if _row is not None:
                        _xo, _yo = _ero_hb(_hud_surf)
                        _WL_hb.add_icon_offset(pos.x, _world_y_top, _row,
                                              x_offset=_xo, y_offset=_yo,
                                              halign="left", valign="center")

    def _draw_remote_corpses(self, cam_x: float, cam_y: float) -> None:
        """Desenha corpos de mobs mortos recebidos do servidor.

        Visual idêntico ao LootSystem.render_world offline (systems.py:5010-5014):
          elipse 20×12 centrada no tile, cor por estado de loot.
        """
        if not self._remote_corpses:
            return
        from engine.tileset import TILE_SIZE as _TS
        surf = self._zoom_surf
        for corpse_id, (tx, ty) in self._remote_corpses.items():
            # Centro do tile em pixels (world-space → zoom-surface)
            cx = tx * _TS + _TS // 2 - cam_x
            cy = ty * _TS + _TS // 2 - cam_y
            loot_data = self._available_loot.get(corpse_id)
            if loot_data is not None:
                # Lê estado real do Corpse ECS local (fonte da verdade após LOOT_AVAILABLE)
                from engine.components import Corpse as _Corpse
                local_eid  = loot_data.get("local_eid")
                corpse_comp = self.world.get_component(local_eid, _Corpse) if local_eid else None
                if corpse_comp:
                    has_coins = corpse_comp.coins > 0
                    has_items = bool(corpse_comp.loot)
                else:
                    has_coins = False
                    has_items = False
                if has_coins:
                    color = (180, 150, 30)   # dourado — moedas presentes
                elif has_items:
                    color = (120, 80, 40)    # marrom — só itens
                else:
                    color = (60, 40, 20)     # marrom escuro — vazio
            else:
                color = (60, 40, 20)         # marrom escuro — outro player, sem info
            rect = (int(cx - 10), int(cy - 6), 20, 12)
            pygame.draw.ellipse(surf, color, rect)
            pygame.draw.ellipse(surf, (80, 55, 25), rect, 1)

    def _remove_remote_player_entity(self, server_eid: int) -> None:
        local_eid = self._remote_players.pop(server_eid, None)
        self._remote_player_move_queues.pop(server_eid, None)
        self._remote_step_timers.pop(server_eid, None)
        if local_eid is not None:
            # Se o player local estava perseguindo esta entidade, guarda o server_eid
            # para reacquirir o alvo quando o player remoto respawnar.
            from engine.components import CombatState as _CStRm
            _cs_rm = self.world.get_component(self.player_entity, _CStRm)
            if _cs_rm and _cs_rm.target_entity_id == local_eid and _cs_rm.is_pursuing:
                self._pvp_respawn_target = server_eid
                # Mantém is_pursuing para indicar intenção de continuar perseguindo
                # _process_target limpará target_entity_id quando a entidade sumir
            try:
                self.world.remove_entity(local_eid)
            except Exception:
                pass

    def _draw_remote_players(self, cam_x: float, cam_y: float) -> None:
        """HUD completa (nível + HP + nome) dos jogadores remotos. Posição
        lida do ECS (TileMovementSystem anima).

        Sem gate de fog of war — outro jogador real dentro do AOI sempre é visível
        (invisibilidade é regra do servidor: CombatState.is_visible/_can_see), igual
        ao corpo dele no RenderSystem.

        Fundo+barras+número do nível saem como UMA ÚNICA Surface (ver
        ui/hud_bars.py) inteira em WORLD_LABELS (screen-space) — nunca
        mais divididos entre espaço de mundo e espaço de tela (causava um
        bug real: número "flutuando" fora da caixinha, ver
        ARQUITETURA_ONLINE.md 23.9). XP/recurso não existem aqui (player
        remoto não expõe esse dado pro cliente, só o dono vê o próprio) —
        as duas linhas ficam vazias (só o "trilho" do asset aparece, sem
        preenchimento).
        """
        if not self._remote_players:
            return
        from engine.components import Position, RemoteControlled
        from ui.world_labels import WORLD_LABELS as _WL_rp
        from ui.hud_bars import build_player_hud as _bph_rp, HUD_GAP_PX as _HGP_rp

        if not hasattr(self, '_player_level_font'):
            from ui.fonts import make as _make_name_rp2
            from ui.hud_bars import LEVEL_FONT_SIZE as _LFS_rp
            self._player_level_font = _make_name_rp2(_LFS_rp * 2)

        from engine.tileset import TILE_SIZE as _TS_rp
        _sprite_h = _TS_rp - 4   # mesma convenção de altura já usada aqui (W = H = TILE_SIZE-4)

        from ui.hud_bars import DISPOSITION_HP_COLORS as _DISP_rp, HP_COLOR as _HPC_rp
        _duel_opp    = getattr(self, "_duel_opponent_local_val", -1)
        _arena_match = getattr(self, "_arena_in_match_val", False)
        _arena_opps  = getattr(self, "_arena_opponents_server_val", set())
        for server_eid, local_eid in self._remote_players.items():
            pos = self.world.get_component(local_eid, Position)
            rc  = self.world.get_component(local_eid, RemoteControlled)
            if not pos or not rc:
                continue
            # Dentro da própria partida de arena, esconde o nameplate de
            # quem já morreu — reduz "spam" visual de fantasmas parados até
            # saírem (pedido do usuário 21/07/2026). Corpo/sprite continua
            # visível (já tingido por _handle_msg_entity_death), só a
            # HUD flutuante some. rc.hp é o sinal de morte já mantido em
            # dia pra players remotos (_handle_msg_entity_death zera na
            # hora, revive/saída da arena restaura via STATS_UPDATE
            # equivalente) — não precisa de GhostState aqui.
            if _arena_match and rc.hp <= 0:
                continue
            ratio = max(0.0, min(1.0, rc.hp / max(1, rc.hp_max)))
            # Oponente de duelo OU do time adversário na arena: barra + nome
            # vermelhos enquanto durar (client/duel_handlers.py mantém
            # _duel_opponent_local_val; arena usa _arena_opponents_server_val
            # — SÓ o outro time fica hostil, nunca o próprio, ver
            # client/arena_handlers.py::_arena_opponents_server).
            _is_duel_opp  = (local_eid == _duel_opp)
            _is_arena_opp = server_eid in _arena_opps
            _is_hostile_rp = _is_duel_opp or _is_arena_opp
            _hp_col_rp   = _DISP_rp["hostil"] if _is_hostile_rp else _HPC_rp
            _name_col_rp = (255, 90, 90) if _is_hostile_rp else (255, 255, 200)
            _hud_surf = _bph_rp(ratio, 0.0, 0.0, (0, 0, 0, 0), rc.level,
                                self._player_level_font, hp_color=_hp_col_rp)
            _world_y_top = pos.y - _sprite_h / 2
            _WL_rp.add_icon(pos.x, _world_y_top, _hud_surf,
                            stack_key=local_eid, gap_before=_HGP_rp)
            # self.font_sm: MESMO objeto de fonte da janela de chat (ver
            # comentário irmão em _draw_mob_hp_bars acima).
            _WL_rp.add_text(pos.x, _world_y_top, rc.name, self.font_sm,
                            _name_col_rp, stack_key=local_eid, gap_before=2)
