"""
network_handlers.py — Mixin com os handlers de mensagens de rede do cliente.
Separado de game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.world, self._my_eid, self._net e os demais atributos referenciados aqui.
"""
from components import PlayerSkills, TileMovement
from tileset import TILE_SIZE
from combat_log import LOG
from floating_text import PROC
from sound_manager import SOUNDS


class NetworkHandlers:
    def _handle_net_message(self, msg_type, payload: dict) -> None:
        from shared.messages import MsgType
        if msg_type == MsgType.LOGIN_OK:
            self._handle_msg_login_ok(payload)
        elif msg_type == MsgType.LOGIN_ERROR:
            self._handle_msg_login_error(payload)
        elif msg_type == MsgType.WORLD_STATE:
            self._handle_msg_world_state(payload)
        elif msg_type == MsgType.ENTITY_SPAWN:
            self._handle_msg_entity_spawn(payload)
        elif msg_type == MsgType.COMBAT_RESULT:
            self._handle_msg_combat_result(payload)
        elif msg_type == MsgType.SKILL_RESULT:
            self._handle_msg_skill_result(payload)
        elif msg_type == MsgType.ENTITY_DESPAWN:
            self._handle_msg_entity_despawn(payload)
        elif msg_type == MsgType.ENTITY_MOVE:
            self._handle_msg_entity_move(payload)
        elif msg_type == MsgType.AOI_UPDATE:
            self._handle_msg_aoi_update(payload)
        elif msg_type == MsgType.STATS_UPDATE:
            self._handle_msg_stats_update(payload)
        elif msg_type == MsgType.PLAYER_DEATH:
            self._handle_msg_player_death(payload)
        elif msg_type == MsgType.LOOT_AVAILABLE:
            self._handle_msg_loot_available(payload)
        elif msg_type == MsgType.SOUND_EVENT:
            self._handle_msg_sound_event(payload)
        elif msg_type == MsgType.LOOT_RESULT:
            self._handle_msg_loot_result(payload)
        elif msg_type == MsgType.BUY_RESULT:
            self._handle_msg_buy_result(payload)
        elif msg_type == MsgType.SELL_RESULT:
            self._handle_msg_sell_result(payload)
        elif msg_type == MsgType.PONG:
            self._handle_msg_pong(payload)


    def _handle_msg_login_ok(self, payload: dict) -> None:
        from components import TileMovement
        self._my_eid = payload.get("eid", -1)
        char = payload.get("char", {})
        tx   = int(char.get("tile_x", 10))
        ty   = int(char.get("tile_y", 10))
        # Sincroniza posição local com o servidor — evita conflito com save offline
        from components import Position
        from shared.constants import TILE_SIZE as _TS
        tm = self.world.get_component(self.player_entity, TileMovement)
        if tm:
            tm.current_tile_x = tx;  tm.current_tile_y = ty
            tm.target_tile_x  = tx;  tm.target_tile_y  = ty
        pos = self.world.get_component(self.player_entity, Position)
        if pos:
            pos.x = tx * _TS + _TS // 2;  pos.y = ty * _TS + _TS // 2
            pos.prev_x = pos.x;            pos.prev_y = pos.y
        self._net_last_tx = tx
        self._net_last_ty = ty
        # Sincroniza HP do player com o servidor
        from components import CombatStats, CharacterStats as _CS_login
        from stats_system import apply_char_stats_to_combat, sync_attack_interval, process_levelups, CLASS_BASE_STATS
        cs        = self.world.get_component(self.player_entity, CombatStats)
        char_stat = self.world.get_component(self.player_entity, _CS_login)
        srv_hp     = payload.get("hp",     0)
        srv_hp_max = payload.get("hp_max", 0)
        if cs and srv_hp_max > 0:
            cs.max_hp     = srv_hp_max
            cs.current_hp = srv_hp

        # Restaura level/XP/atributos do save do servidor
        import json as _jl
        _stats_raw = char.get("stats_json", "{}")
        _stats_s   = _jl.loads(_stats_raw) if isinstance(_stats_raw, str) else {}
        _cls_s  = char.get("class_id", "guerreiro")
        _base_s = CLASS_BASE_STATS.get(_cls_s, CLASS_BASE_STATS["guerreiro"])
        if char_stat:
            # Sempre define class_id para garantir CLASS_MELEE_OVERRIDES
            char_stat.class_id = _cls_s
            if _stats_s:
                # Personagem com save: restaura tudo
                char_stat.level            = int(_stats_s.get("level",         char.get("level", 1)))
                char_stat.current_xp       = int(_stats_s.get("current_xp",   0))
                char_stat.xp_to_next_level = _CS_login.xp_for_level(char_stat.level)
                char_stat.strength         = int(_stats_s.get("strength",     _base_s["strength"]))
                char_stat.intelligence     = int(_stats_s.get("intelligence", _base_s["intelligence"]))
                char_stat.agility          = int(_stats_s.get("agility",      _base_s["agility"]))
                char_stat.vitality         = int(_stats_s.get("vitality",     _base_s["vitality"]))
                char_stat.defense          = int(_stats_s.get("defense",      _base_s["defense"]))
                _gold_s = int(_stats_s.get("gold", 0))
                if _gold_s > 0:
                    from components import Wallet as _W_login
                    wallet_login = self.world.get_component(self.player_entity, _W_login)
                    if wallet_login:
                        wallet_login.gold = _gold_s
            else:
                # Personagem novo: usa base da classe
                char_stat.strength     = _base_s["strength"]
                char_stat.intelligence = _base_s["intelligence"]
                char_stat.agility      = _base_s["agility"]
                char_stat.vitality     = _base_s["vitality"]
                char_stat.defense      = _base_s["defense"]

            # SEMPRE recalcula CombatStats — garante CLASS_MELEE_OVERRIDES aplicado
            from components import PermanentStats as _PS_login
            from components import Equipment as _EqLogin
            perm_login = self.world.get_component(self.player_entity, _PS_login)
            eq_login   = self.world.get_component(self.player_entity, _EqLogin)
            if cs:
                apply_char_stats_to_combat(char_stat, cs, perm_login)
                sync_attack_interval(cs, eq_login)
        # Restaura equipment/talents/skills — aplica modifiers ANTES de definir HP
        self._restore_save_state(char)
        # Aplica layout da hotbar a partir de config.json (fonte de verdade local).
        # _restore_save_state só restaura learned_skill_ids; posicionamento é UI.
        self._logged_char_name = char.get("name", "")
        self._apply_hotbar_config(char_name=self._logged_char_name)
        # Define HP DEPOIS dos modifiers (max_hp já inclui bônus de equipamento)
        if cs and srv_hp > 0:
            cs.current_hp = min(srv_hp, cs.max_hp)
        print(f"[Client] login ok  eid={self._my_eid}  "
              f"user={char.get('name','?')}  Nv{char_stat.level if char_stat else 1}"
              f"  tile=({tx},{ty})  hp={srv_hp}/{srv_hp_max}")
        # Personagem carregado — marca servidor como pronto.
        # O jogo só entra quando _server_ready=True E _loading_min_t <= 0.
        self._server_ready = True

    def _handle_msg_login_error(self, payload: dict) -> None:
        print(f"[Client] login erro: {payload.get('reason')}")
        self._server_ready = True  # sai da loading screen mesmo com erro

    def _handle_msg_world_state(self, payload: dict) -> None:
        for ent in payload.get("entities", []):
            eid  = ent.get("eid", -1)
            kind = ent.get("kind", "player")
            if eid == -1 or eid == self._my_eid:
                continue
            if kind == "enemy":
                self._spawn_remote_mob(eid, ent)
            else:
                self._spawn_remote_player_entity(eid, {
                    "tx": ent.get("tx", 0), "ty": ent.get("ty", 0),
                    "name":     ent.get("name", "?"),
                    "class_id": ent.get("class_id", "guerreiro"),
                    "hp":       ent.get("hp", 100),
                    "hp_max":   ent.get("hp_max", 100),
                })

    def _handle_msg_entity_spawn(self, payload: dict) -> None:
        eid  = payload.get("eid", -1)
        kind = payload.get("kind", "player")
        # Verifica corpse ANTES do guard eid==-1 (corpse usa eid negativo, -1 inclusive)
        if kind == "corpse":
            corpse_id = -eid
            self._remote_corpses[corpse_id] = (payload.get("tx", 0), payload.get("ty", 0))
        elif eid == -1 or eid == self._my_eid:
            pass
        elif kind == "enemy":
            self._spawn_remote_mob(eid, payload)
        else:
            self._spawn_remote_player_entity(eid, {
                "tx": payload.get("tx", 0), "ty": payload.get("ty", 0),
                "name":     payload.get("name", "?"),
                "class_id": payload.get("class_id", "guerreiro"),
                "hp":       payload.get("hp", 100),
                "hp_max":   payload.get("hp_max", 100),
            })

    def _handle_msg_combat_result(self, payload: dict) -> None:
        self._apply_combat_result(payload)

    def _handle_msg_skill_result(self, payload: dict) -> None:
        caster_eid = payload.get("caster_eid", -1)
        sid        = payload.get("sid", "")
        targets    = payload.get("targets", [])
        # Som da skill — confirmado pelo servidor (evita som sem dano em kiting)
        if sid:
            from skill_config import SKILL_CATALOG as _SC_snd
            from components import Position as _PosSR
            _sk_entry  = _SC_snd.get(sid, {})
            _snd_name  = (_sk_entry.get("sound") if isinstance(_sk_entry, dict) else None) or f"skill_{sid}"
            if caster_eid == self._my_eid:
                _failed_sr     = payload.get("failed",        False)
                _ps_sr         = self.world.get_component(self.player_entity, PlayerSkills)
                _cast_started  = payload.get("cast_started",  False)
                _is_completion = payload.get("is_completion", False)
                _is_proj_dmg   = payload.get("is_proj_damage", False)
                if _failed_sr:
                    # Servidor rejeitou: limpa pending e restaura carga consumida
                    if _ps_sr:
                        _srv_cd_fail = payload.get("cooldown", 0)
                        for _sk_sr in _ps_sr.skills:
                            if _sk_sr and _sk_sr.skill_id == sid:
                                _sk_sr._server_pending         = False
                                _sk_sr._server_pending_timeout = 0.0
                                if _sk_sr.max_charges > 0 and _sk_sr.charges < _sk_sr.max_charges:
                                    _sk_sr.charges += 1
                                if _srv_cd_fail > 0:
                                    _sk_sr.current_cooldown = float(_srv_cd_fail)
                    # Cancela SpellCast visual para o cast bar desaparecer
                    from components import SpellCast as _SCFail, CombatState as _CSFail
                    _sc_fail = self.world.get_component(self.player_entity, _SCFail)
                    if _sc_fail and _sc_fail.spell_id == sid:
                        self.world.remove_component(self.player_entity, _SCFail)
                    # Garante que is_casting é limpo — can_act() depende disso
                    _cs_fail = self.world.get_component(self.player_entity, _CSFail)
                    if _cs_fail:
                        _cs_fail.is_casting = False
                    # Exibe motivo da rejeição se o servidor enviou
                    _fail_reason = payload.get("reason", "")
                    if _fail_reason:
                        from floating_text import WARN as _WARN_fail
                        _WARN_fail.add(_fail_reason)
                elif _cast_started:
                    # Cast com tempo aceito pelo servidor: só GCD + limpa pending.
                    # Som e cooldown chegam no is_completion quando a spell realmente dispara.
                    # Novo cast aceito: limpa flag de cancelamento anterior desta spell.
                    self._cancelled_spell_ids.discard(sid)
                    if _ps_sr:
                        _ps_sr.gcd_timer = PlayerSkills.GCD_DURATION
                        for _sk_sr in _ps_sr.skills:
                            if _sk_sr and _sk_sr.skill_id == sid:
                                _sk_sr._server_pending         = False
                                _sk_sr._server_pending_timeout = 0.0
                elif _is_completion:
                    # Cast completou no servidor: som + cooldown. GCD já foi aplicado.
                    SOUNDS.play_skill(_snd_name)
                    if _ps_sr:
                        _srv_cd = payload.get("cooldown")
                        for _sk_sr in _ps_sr.skills:
                            if _sk_sr and _sk_sr.skill_id == sid:
                                _sk_sr._server_pending         = False
                                _sk_sr._server_pending_timeout = 0.0
                                _sk_sr.current_cooldown = float(_srv_cd) if _srv_cd is not None else _sk_sr.cooldown
                elif _is_proj_dmg:
                    pass  # só mostra dano — GCD/CD/som já foram em cast_started/is_completion
                else:
                    # Skill instantânea: GCD + cooldown + som tudo junto (como offline).
                    SOUNDS.play_skill(_snd_name)
                    if _ps_sr:
                        _ps_sr.gcd_timer = PlayerSkills.GCD_DURATION
                        _srv_cd = payload.get("cooldown")
                        for _sk_sr in _ps_sr.skills:
                            if _sk_sr and _sk_sr.skill_id == sid:
                                _sk_sr._server_pending         = False
                                _sk_sr._server_pending_timeout = 0.0
                                _sk_sr.current_cooldown = float(_srv_cd) if _srv_cd is not None else _sk_sr.cooldown
            elif caster_eid in self._remote_players:
                # Player remoto: posicional
                _cast_local = self._remote_players[caster_eid]
                _cast_pos   = self.world.get_component(_cast_local, _PosSR)
                if _cast_pos:
                    _slx, _sly = self._player_world_pos()
                    SOUNDS.play_skill_at(_snd_name, _cast_pos.x, _cast_pos.y,
                                         _slx, _sly, base=0.85)
        # Escudo de Fogo confirmado: adiciona FireShieldEffect no cliente para visual + timer
        if caster_eid == self._my_eid and sid == "escudo_fogo" and not payload.get("failed"):
            from components import FireShieldEffect as _FSEcl
            if not self.world.get_component(self.player_entity, _FSEcl):
                self.world.add_component(self.player_entity, _FSEcl(duration=15.0))

        # Bloco de Gelo confirmado: adiciona IceBlockEffect no cliente.
        # Sem isso, IceBlockSystem nunca roda online e a cura (10%/s) nunca
        # aparece no HP bar — o jogador só vê a cura toda de uma vez quando
        # o servidor sincroniza o HP por outro motivo.
        if caster_eid == self._my_eid and sid == "bloco_de_gelo" and not payload.get("failed"):
            from components import IceBlockEffect as _IBEcl, CombatState as _CStIB
            if not self.world.get_component(self.player_entity, _IBEcl):
                self.world.add_component(self.player_entity, _IBEcl(
                    duration=5.0, elapsed=0.0, heal_interval=1.0, last_heal=0.0,
                ))
                _cst_ib = self.world.get_component(self.player_entity, _CStIB)
                if _cst_ib:
                    _cst_ib.is_stunned = True
                    _cst_ib.is_immune  = True

        # Consome carga livre de Executar ao usar a skill
        if caster_eid == self._my_eid and sid == "executar":
            from components import CharacterStats as _CSexec
            _char_exec = self.world.get_component(self.player_entity, _CSexec)
            if _char_exec and _char_exec.free_executar_charges > 0:
                _char_exec.free_executar_charges -= 1

        # Procs sincronizados pelo servidor
        if caster_eid == self._my_eid and payload.get("assassino_proc"):
            from components import CharacterStats as _CSproc
            _char_proc = self.world.get_component(self.player_entity, _CSproc)
            if _char_proc:
                _char_proc.free_executar_charges = 1
                from combat_log import LOG as _LOG_proc
                _LOG_proc.add("Assassino: Executar disponivel! (sem custo, sem restricao de HP)",
                              (255, 80, 80))
                PROC.add("Assassino!", (255, 80, 80))

        if caster_eid == self._my_eid and payload.get("fire_instant_proc"):
            from components import CharacterStats as _CSfi
            _char_fi = self.world.get_component(self.player_entity, _CSfi)
            if _char_fi:
                _char_fi.fire_instant_ready = True
                from combat_log import LOG as _LOG_fi
                _LOG_fi.add("Chama Interna: proxima Bola de Fogo instantanea e gratis!",
                            (255, 160, 60))
                PROC.add("Chama Interna!", (255, 160, 60))

        if caster_eid == self._my_eid and payload.get("lapso_proc"):
            _lp = payload["lapso_proc"]
            _lp_bonus = float(_lp.get("bonus", 0.0))
            _lp_dur   = float(_lp.get("duration", 5.0))
            if _lp_bonus > 0:
                from components import CombatStats as _CSlp
                from components import Modifier as _Modlp
                from stat_fns import add_timed_modifier as _atm_lp
                _cs_lp = self.world.get_component(self.player_entity, _CSlp)
                if _cs_lp:
                    _atm_lp(_cs_lp, _Modlp("crit_rating", _lp_bonus, "flat"),
                            _lp_dur, "lapso_elemental")
                from combat_log import LOG as _LOG_lp
                _LOG_lp.add(f"Lapso Elemental: +{int(_lp_bonus*100)}% Critico por {_lp_dur:.0f}s!",
                            (255, 180, 50))
                PROC.add("Lapso Elemental!", (255, 180, 50))

        # BdF is_completion: servidor envia projectile_target → cria projétil aqui.
        # is_proj_damage: dano confirmado após PROJECTILE_HIT_CS → mostra números.
        _bdf_deferred: set = set()
        if caster_eid == self._my_eid and sid == "bola_de_fogo":
            from components import PlayerProjectile as _PPcomp, Position as _PPpos2
            _is_compl = payload.get("is_completion", False)
            _is_pdmg  = payload.get("is_proj_damage", False)

            if _is_compl:
                # Cast foi cancelado localmente? Ignora is_completion tardio do servidor.
                # O servidor também removeu a entrada de _spells_in_flight_queue ao
                # receber CANCEL_CAST, então nenhum dano seria aplicado de qualquer forma.
                if sid in self._cancelled_spell_ids:
                    self._cancelled_spell_ids.discard(sid)
                else:
                    # Cria projétil — alvo pode ser mob remoto ou player remoto (PvP)
                    _proj_srv = payload.get("projectile_target", -1)
                    _proj_loc = -1
                    if _proj_srv != -1:
                        _proj_loc = self._remote_mobs.get(_proj_srv, -1)
                        if _proj_loc == -1:
                            _proj_loc = self._remote_players.get(_proj_srv, -1)
                    if _proj_loc != -1:
                        self._spell_cast_system._launch_fireball(self.player_entity, _proj_loc)
                        for _peid, _pp, _ in self.world.get_entities_with(_PPcomp, _PPpos2):
                            if (_pp.attacker_id == self.player_entity
                                    and _pp.target_id == _proj_loc
                                    and _pp.target_server_id == -1):
                                _pp.target_server_id = _proj_srv
                                break
                    if _proj_srv != -1:
                        _bdf_deferred.add(_proj_srv)

            elif _is_pdmg:
                # Dano confirmado após projétil colidir → mostra números
                for t in targets:
                    _t_srv = t.get("eid", -1)
                    _t_hp  = t.get("hp_after", -1)
                    if _t_hp >= 0 and _t_srv != -1:
                        _meta_pd = self._meta(_t_srv)
                        if _meta_pd:
                            _meta_pd.hp = _t_hp
                    _cr_pdmg = {
                        "attacker":      caster_eid,
                        "target":        _t_srv,
                        "damage":        t.get("damage",  0),
                        "outcome":       t.get("outcome", "hit"),
                        "hp_after":      _t_hp,
                        "source":        "skill",
                        "sid":           sid,
                    }
                    # Propaga mob_slow_mult para _apply_combat_result sincronizar slow
                    if "mob_slow_mult" in t:
                        _cr_pdmg["mob_slow_mult"] = t["mob_slow_mult"]
                    self._apply_combat_result(_cr_pdmg)
                    _bdf_deferred.add(_t_srv)

        # Archer is_proj_damage: dano confirmado após PROJECTILE_HIT_CS → mostra FLT imediato.
        # O projétil já colidiu — não há entidade de flecha para consumir pending_arrow_impacts.
        _ARCHER_PROJ_SKILLS = {"flecha_reiterada", "picada_escorpiao", "tiro_repulsivo"}
        if (caster_eid == self._my_eid
                and sid in _ARCHER_PROJ_SKILLS
                and _is_proj_dmg):
            for t in targets:
                _t_srv_ap = t.get("eid", -1)
                _t_hp_ap  = t.get("hp_after", -1)
                _cr_ap = {
                    "attacker":       caster_eid,
                    "target":         _t_srv_ap,
                    "damage":         t.get("damage",  0),
                    "outcome":        t.get("outcome", "hit"),
                    "hp_after":       _t_hp_ap,
                    "source":         "skill",
                    "sid":            sid,
                    "is_proj_damage": True,
                }
                if "mob_slow_mult" in t:
                    _cr_ap["mob_slow_mult"] = t["mob_slow_mult"]
                self._apply_combat_result(_cr_ap)
                _bdf_deferred.add(_t_srv_ap)

        for t in targets:
            if t.get("eid", -1) in _bdf_deferred:
                continue   # FLT será exibido ao impacto do projétil
            _cr_t = {
                "attacker": caster_eid,
                "target":   t.get("eid",     -1),
                "damage":   t.get("damage",   0),
                "outcome":  t.get("outcome",  "hit"),
                "hp_after": t.get("hp_after", -1),
                "source":   "skill",
                "sid":      sid,
            }
            if "mob_slow_mult" in t:
                _cr_t["mob_slow_mult"] = t["mob_slow_mult"]
            self._apply_combat_result(_cr_t)
        # LOG e aplicação local de efeitos confirmados pelo servidor
        if caster_eid == self._my_eid:
            from status_effects_data import EFFECT_DEFS as _EDEFS_sr
            from core_systems import apply_effect as _ae_apply
            for t in targets:
                _ae = t.get("applied_effects", [])
                if not _ae:
                    continue
                _t_srv = t.get("eid", -1)
                # Busca mob remoto ou player remoto (PvP) como alvo local
                _t_local = self._remote_mobs.get(_t_srv)
                if _t_local is None:
                    _t_local = self._remote_players.get(_t_srv)
                if _t_local is not None:
                    from components import EntityIdentity as _EI_sr, RemoteControlled as _RCae
                    _ident_sr = self.world.get_component(_t_local, _EI_sr)
                    _rc_ae    = self.world.get_component(_t_local, _RCae)
                    _tname = (_ident_sr.name if _ident_sr
                              else (_rc_ae.name if _rc_ae else "Alvo"))
                else:
                    _tname = "Alvo"
                _eff_durs_sr = t.get("effect_durations", {})
                for _ef in _ae:
                    _defn_sr = _EDEFS_sr.get(_ef)
                    _elabel  = _defn_sr.label if _defn_sr else _ef
                    LOG.add(f"{_tname} recebeu: {_elabel}!", (255, 200, 80))
                    # Aplica efeito no alvo local (mob ou player remoto PvP).
                    # Root: para interpolação, evitando snapback.
                    # Slow: agora aplicado localmente para sincronizar animação
                    # com o servidor (slow muda slow_mult no TileMovement).
                    if _t_local is not None:
                        _dur_sr = _eff_durs_sr.get(_ef, 5.0)
                        _ae_apply(self.world, _t_local, _ef, _dur_sr)

        # Flecha Reiterada is_completion: cria projéteis visuais com delay sequencial.
        # Primeira flecha envia PROJECTILE_HIT_CS ao colidir; FLT exibido no is_proj_damage.
        _fr_srv_tgt = payload.get("projectile_target", -1)
        if (caster_eid == self._my_eid
                and sid == "flecha_reiterada"
                and payload.get("is_completion")
                and not payload.get("failed")
                and _fr_srv_tgt != -1):
            from components import PlayerProjectile as _PPfr
            from components import Position as _PosFR
            from skill_config import SKILL_CATALOG as _SC_fr2
            _fr_params  = _SC_fr2.get("flecha_reiterada", {}).get("params", {})
            _fr_delay   = _fr_params.get("arrow_delay", 0.25)
            _fr_ap_mult = _fr_params.get("ap_multiplier", 2.0)
            # Servidor informa quantas flechas criar (inclui talento Sequência Final)
            _fr_n       = int(payload.get("arrow_count",
                              _fr_params.get("arrow_count", 2)))
            _fr_loc_tgt = self._remote_mobs.get(
                _fr_srv_tgt,
                self._remote_players.get(_fr_srv_tgt, -1),
            )
            # Mob pode ter despawnado antes desta mensagem (WORLD_STATE antes de SKILL_RESULT).
            # Usa o cache preenchido por _apply_combat_result para recuperar local_eid + posição.
            _fr_cached_x = _fr_cached_y = None
            if _fr_loc_tgt == -1:
                _fr_cache = self._fr_pending_target.pop(_fr_srv_tgt, None)
                if _fr_cache:
                    _fr_loc_tgt, _fr_cached_x, _fr_cached_y = _fr_cache
            else:
                self._fr_pending_target.pop(_fr_srv_tgt, None)
            if _fr_loc_tgt != -1:
                _pl_pos_fr  = self.world.get_component(self.player_entity, _PosFR)
                _tgt_pos_fr = self.world.get_component(_fr_loc_tgt, _PosFR)
                if _pl_pos_fr:
                    if _fr_cached_x is not None:
                        _tlx, _tly = _fr_cached_x, _fr_cached_y
                    else:
                        _tlx = _tgt_pos_fr.x if _tgt_pos_fr else _pl_pos_fr.x
                        _tly = _tgt_pos_fr.y if _tgt_pos_fr else _pl_pos_fr.y
                    _fr_speeds = [700.0, 640.0, 580.0]
                    for _i in range(_fr_n):
                        _peid_fr = self.world.create_entity()
                        self.world.add_component(_peid_fr, _PosFR(
                            x=_pl_pos_fr.x, y=_pl_pos_fr.y,
                            prev_x=_pl_pos_fr.x, prev_y=_pl_pos_fr.y,
                        ))
                        # Cada flecha envia PROJECTILE_HIT_CS → 1 dano separado por flecha
                        self.world.add_component(_peid_fr, _PPfr(
                            spell_id="flecha_reiterada",
                            attacker_id=self.player_entity,
                            target_id=_fr_loc_tgt,
                            speed=_fr_speeds[_i] if _i < len(_fr_speeds) else 600.0,
                            dmg_weapon_pct=1.0,
                            dmg_sp_coeff=0.0,
                            color=(101, 67, 33),
                            damage_type="physical",
                            launch_delay=_fr_delay * _i,
                            ap_multiplier=_fr_ap_mult,
                            guaranteed_hit=True,
                            target_last_x=_tlx,
                            target_last_y=_tly,
                            target_server_id=_fr_srv_tgt,
                        ))
                    SOUNDS.play_random(["arrow_release_1", "arrow_release_2"],
                                       channel_group=(10, 11))

        # Picada de Escorpião is_completion: cria projétil visual.
        # Primeira (única) flecha envia PROJECTILE_HIT_CS ao colidir.
        _pe_srv_tgt = payload.get("projectile_target", -1)
        if (caster_eid == self._my_eid
                and sid == "picada_escorpiao"
                and payload.get("is_completion")
                and not payload.get("failed")
                and sid not in self._cancelled_spell_ids
                and _pe_srv_tgt != -1):
            from components import PlayerProjectile as _PPpe
            from components import Position as _PosPS
            from skill_config import SKILL_CATALOG as _SC_pe
            _pe_ap = _SC_pe.get("picada_escorpiao", {}).get("params", {}).get("ap_multiplier", 1.5)
            _pe_loc = self._remote_mobs.get(
                _pe_srv_tgt, self._remote_players.get(_pe_srv_tgt, -1))
            if _pe_loc != -1:
                _pl_pos_pe  = self.world.get_component(self.player_entity, _PosPS)
                _tgt_pos_pe = self.world.get_component(_pe_loc, _PosPS)
                if _pl_pos_pe:
                    _pe_tlx = _tgt_pos_pe.x if _tgt_pos_pe else _pl_pos_pe.x
                    _pe_tly = _tgt_pos_pe.y if _tgt_pos_pe else _pl_pos_pe.y
                    _peid_pe = self.world.create_entity()
                    self.world.add_component(_peid_pe, _PosPS(
                        x=_pl_pos_pe.x, y=_pl_pos_pe.y,
                        prev_x=_pl_pos_pe.x, prev_y=_pl_pos_pe.y,
                    ))
                    self.world.add_component(_peid_pe, _PPpe(
                        spell_id         = "picada_escorpiao",
                        attacker_id      = self.player_entity,
                        target_id        = _pe_loc,
                        speed            = 700.0,
                        dmg_weapon_pct   = 1.0,
                        dmg_sp_coeff     = 0.0,
                        color            = (101, 67, 33),
                        damage_type      = "physical",
                        ap_multiplier    = _pe_ap,
                        guaranteed_hit   = True,
                        target_last_x    = _pe_tlx,
                        target_last_y    = _pe_tly,
                        target_server_id = _pe_srv_tgt,
                    ))
                    SOUNDS.play_random(["arrow_release_1", "arrow_release_2"],
                                       channel_group=(10, 11))

        # Tiro Repulsivo is_completion: cria projétil visual.
        # Knockback/stun aplicados pelo servidor após PROJECTILE_HIT_CS.
        _tr_srv_tgt = payload.get("projectile_target", -1)
        if (caster_eid == self._my_eid
                and sid == "tiro_repulsivo"
                and payload.get("is_completion")
                and not payload.get("failed")
                and sid not in self._cancelled_spell_ids
                and _tr_srv_tgt != -1):
            from components import PlayerProjectile as _PPtrep
            from components import Position as _PosTR
            from skill_config import SKILL_CATALOG as _SC_tr
            _tr_ap = _SC_tr.get("tiro_repulsivo", {}).get("params", {}).get("ap_multiplier", 1.5)
            _tr_loc = self._remote_mobs.get(
                _tr_srv_tgt, self._remote_players.get(_tr_srv_tgt, -1))
            if _tr_loc != -1:
                _pl_pos_tr  = self.world.get_component(self.player_entity, _PosTR)
                _tgt_pos_tr = self.world.get_component(_tr_loc, _PosTR)
                if _pl_pos_tr:
                    _tr_tlx = _tgt_pos_tr.x if _tgt_pos_tr else _pl_pos_tr.x
                    _tr_tly = _tgt_pos_tr.y if _tgt_pos_tr else _pl_pos_tr.y
                    _peid_tr = self.world.create_entity()
                    self.world.add_component(_peid_tr, _PosTR(
                        x=_pl_pos_tr.x, y=_pl_pos_tr.y,
                        prev_x=_pl_pos_tr.x, prev_y=_pl_pos_tr.y,
                    ))
                    self.world.add_component(_peid_tr, _PPtrep(
                        spell_id         = "tiro_repulsivo",
                        attacker_id      = self.player_entity,
                        target_id        = _tr_loc,
                        speed            = 800.0,
                        dmg_weapon_pct   = 1.0,
                        dmg_sp_coeff     = 0.0,
                        color            = (80, 160, 255),
                        damage_type      = "physical",
                        ap_multiplier    = _tr_ap,
                        guaranteed_hit   = True,
                        target_last_x    = _tr_tlx,
                        target_last_y    = _tr_tly,
                        target_server_id = _tr_srv_tgt,
                    ))
                    SOUNDS.play_random(["arrow_release_1", "arrow_release_2"],
                                       channel_group=(10, 11))

    def _handle_msg_entity_despawn(self, payload: dict) -> None:
        eid = payload.get("eid", -1)
        if eid < 0:
            # Corpse expirou ou foi saqueado — remove visual
            corpse_id = -eid
            self._remote_corpses.pop(corpse_id, None)
            loot_data = self._available_loot.pop(corpse_id, None)
            if loot_data:
                local_c_eid = loot_data.get("local_eid")
                if local_c_eid is not None:
                    try:
                        self.world.remove_entity(local_c_eid)
                    except Exception:
                        pass
        else:
            self._remove_remote_player_entity(eid)
            # Remove projétil de mob se era um projétil visual
            _proj_local = self._remote_mob_projectiles.pop(eid, None)
            if _proj_local is not None:
                try:
                    self.world.remove_entity(_proj_local)
                except Exception:
                    pass
            # Remove mob do ECS local se era um mob do servidor
            local_eid = self._remote_mobs.pop(eid, None)
            if local_eid is not None:
                from aoi_debug import AOI_DBG
                AOI_DBG.log("DESPAWN_ENT", server_eid=eid, local_eid=local_eid)
                # Salva ghost position ANTES de remover entidade — fallback de FLT
                from components import RemoteEntityMeta as _REM_d2
                _meta_d2 = self.world.get_component(local_eid, _REM_d2)
                if _meta_d2 and (_meta_d2.last_x or _meta_d2.last_y):
                    self._mob_ghost_pos[eid] = (_meta_d2.last_x, _meta_d2.last_y)
                # Som de morte posicional antes de remover a entidade
                try:
                    from components import Position as _PosD, MobSounds as _MSD
                    _pos_d = self.world.get_component(local_eid, _PosD)
                    _snd_d = self.world.get_component(local_eid, _MSD)
                    if _pos_d:
                        _dlx, _dly = self._player_world_pos()
                        SOUNDS.play_mob_sounds_at(_snd_d, "death",
                                                  _pos_d.x, _pos_d.y,
                                                  _dlx, _dly, base=0.8,
                                                  dedup_key=str(local_eid))
                except Exception:
                    pass
                try:
                    self.world.remove_entity(local_eid)
                except Exception:
                    pass

    def _handle_msg_entity_move(self, payload: dict) -> None:
        eid = payload.get("eid", -1)
        if eid == self._my_eid:
            # Servidor corrigiu nossa posição — aplica
            real_tx = payload.get("tx", 0)
            real_ty = payload.get("ty", 0)
            skill_rejected = payload.get("skill_rejected", False)
            player_tm = self.world.get_component(self.player_entity, TileMovement)
            if player_tm:
                # Se cliente já está dashando para o mesmo tile (prediction correta), não interrompe
                if (getattr(player_tm, "is_dash", False) and
                        player_tm.target_tile_x == real_tx and
                        player_tm.target_tile_y == real_ty):
                    pass  # animação em curso bate com posição do servidor — mantém
                elif (player_tm.current_tile_x != real_tx or
                        player_tm.current_tile_y != real_ty or
                        skill_rejected):
                    # Cancela animação de dash se estava em curso (skill rejeitada)
                    if getattr(player_tm, "is_dash", False):
                        player_tm.is_dash      = False
                        player_tm.is_moving    = False
                        player_tm.progress     = 0.0
                    player_tm.current_tile_x = real_tx
                    player_tm.current_tile_y = real_ty
                    player_tm.target_tile_x  = real_tx
                    player_tm.target_tile_y  = real_ty
                    # Sincroniza pixel position — B10
                    from components import Position as _PosSync
                    _ppos = self.world.get_component(self.player_entity, _PosSync)
                    if _ppos:
                        _ppos.x = real_tx * TILE_SIZE + TILE_SIZE / 2
                        _ppos.y = real_ty * TILE_SIZE + TILE_SIZE / 2
                    if skill_rejected:
                        # Feedback imediato: avisa que o dash foi bloqueado
                        from floating_text import FLT
                        from components import Position as _PosRej
                        _pos_rej = self.world.get_component(self.player_entity, _PosRej)
                        if _pos_rej:
                            FLT.add("Bloqueado!", _pos_rej.x, _pos_rej.y,
                                    (255, 80, 80), "small",
                                    target_id=self.player_entity)
        elif eid in self._remote_players:
            self._apply_remote_move(eid, payload.get("tx", 0), payload.get("ty", 0))

    def _handle_msg_aoi_update(self, payload: dict) -> None:
        for m in payload.get("moved", []):
            eid = m.get("eid", -1)
            if eid == self._my_eid:
                continue
            if eid in self._remote_players:
                self._apply_remote_move(eid, m["tx"], m["ty"],
                                        from_tx=m.get("from_tx"),
                                        from_ty=m.get("from_ty"),
                                        is_dash=m.get("is_dash", False))
            elif eid in self._remote_mobs:
                self._move_remote_mob(eid, m["tx"], m["ty"],
                                      m.get("from_tx"), m.get("from_ty"))
        for sp in payload.get("spawned", []):
            eid  = sp.get("eid", -1)
            kind = sp.get("kind", "player")
            if eid != -1 and eid != self._my_eid:
                if kind in ("enemy", "mob_projectile"):
                    from aoi_debug import AOI_DBG
                    AOI_DBG.log("SPAWN_RAW", server_eid=eid, kind=kind,
                                tx=sp.get("tx"), ty=sp.get("ty"),
                                already_tracked=(eid in self._remote_mobs))
                if kind == "enemy" and eid not in self._remote_mobs:
                    self._spawn_remote_mob(eid, sp)
                elif kind == "mob_projectile":
                    self._spawn_mob_projectile(eid, sp)
        for sp in payload.get("spawned", []):
            eid  = sp.get("eid", -1)
            kind = sp.get("kind", "player")
            # Ignora enemy e mob_projectile (já processados acima) e o próprio player
            if eid != -1 and eid != self._my_eid \
                    and kind not in ("enemy", "mob_projectile"):
                self._spawn_remote_player_entity(eid, {
                    "tx": sp.get("tx", 0), "ty": sp.get("ty", 0),
                    "name":     sp.get("name", "?"),
                    "class_id": sp.get("class_id", "guerreiro"),
                    "hp":       sp.get("hp", 100),
                    "hp_max":   sp.get("hp_max", 100),
                })
        # Status effects sync (antes de combat para ter CC certo na animação)
        for eff_payload in payload.get("effects", []):
            if eff_payload.get("eid") == self._my_eid:
                self._sync_player_effects(eff_payload["effects"])
        # Status effects em mobs remotos (ícones acima da barra + LOG de CC)
        _mob_efx = payload.get("mob_effects", {})
        if _mob_efx:
            self._sync_mob_effects(_mob_efx)
        # Combat ANTES de despawned: garante floating text do golpe fatal
        # antes do mob ser removido de _remote_mobs
        for cr in payload.get("combat", []):
            self._apply_combat_result(cr)
        for eid in payload.get("despawned", []):
            self._remove_remote_player_entity(eid)
            self._remote_players.pop(eid, None)
            local_eid = self._remote_mobs.pop(eid, None)
            self._mob_move_queues.pop(eid, None)
            if local_eid is not None:
                from aoi_debug import AOI_DBG
                AOI_DBG.log("DESPAWN_AOI", server_eid=eid, local_eid=local_eid)
                # Salva ghost position ANTES de remover entidade — fallback de FLT
                from components import RemoteEntityMeta as _REM_d
                _meta_d = self.world.get_component(local_eid, _REM_d)
                if _meta_d and (_meta_d.last_x or _meta_d.last_y):
                    self._mob_ghost_pos[eid] = (_meta_d.last_x, _meta_d.last_y)
                # Som de morte ANTES de remover a entidade
                try:
                    from components import Position as _PosD2, MobSounds as _MSD2
                    _pos_d2 = self.world.get_component(local_eid, _PosD2)
                    _snd_d2 = self.world.get_component(local_eid, _MSD2)
                    if _pos_d2:
                        _dlx2, _dly2 = self._player_world_pos()
                        SOUNDS.play_mob_sounds_at(_snd_d2, "death",
                                                  _pos_d2.x, _pos_d2.y,
                                                  _dlx2, _dly2, base=0.85,
                                                  dedup_key=str(local_eid))
                except Exception:
                    pass
                try:
                    self.world.remove_entity(local_eid)
                except Exception:
                    pass

    def _handle_msg_stats_update(self, payload: dict) -> None:
        from components import CombatStats, RemoteControlled
        eid = payload.get("eid", -1)
        # Projétil chegando em mim (PvP): cria projétil puramente visual (sem dano local).
        # Projétil de outro player chegando — cria visual cosmético.
        # proj_target pode ser mob (proj. para mob) ou player (PvP).
        if eid == self._my_eid and payload.get("proj_incoming"):
            _proj_sid    = payload["proj_incoming"]
            _proj_caster = payload.get("proj_caster", -1)
            _proj_tgt    = payload.get("proj_target", -1)
            _caster_local = self._remote_players.get(_proj_caster, -1)
            if _caster_local != -1 and _proj_sid == "bola_de_fogo":
                # Resolve entidade local do alvo: mob remoto ou player remoto
                if _proj_tgt == self._my_eid:
                    _tgt_local = self.player_entity  # sou o alvo (PvP)
                elif _proj_tgt in self._remote_players:
                    _tgt_local = self._remote_players[_proj_tgt]
                else:
                    _tgt_local = self._remote_mobs.get(_proj_tgt, -1)
                if _tgt_local != -1:
                    self._spell_cast_system._launch_fireball(_caster_local, _tgt_local)
                    from components import PlayerProjectile as _PPinc, Position as _PPinc_pos
                    for _ppeid, _pp, _ in self.world.get_entities_with(_PPinc, _PPinc_pos):
                        if (_pp.attacker_id == _caster_local
                                and _pp.target_id == _tgt_local
                                and _pp.target_server_id == -1):
                            _pp.target_server_id = -2  # cosmético: sem PROJECTILE_HIT_CS
                            break

        if eid == self._my_eid:
            cs = self.world.get_component(self.player_entity, CombatStats)
            if cs and "hp" in payload:
                cs.current_hp = payload["hp"]
            if cs and "hp_max" in payload:
                cs.max_hp = payload["hp_max"]
            # Rage/mana/concentração sincronizados após skill consumir recursos
            _srv_rage = payload.get("rage")
            _srv_mana = payload.get("mana")
            _srv_conc = payload.get("concentration")
            if _srv_rage is not None or _srv_mana is not None or _srv_conc is not None:
                from components import CharacterStats as _CSST
                _char_sync = self.world.get_component(self.player_entity, _CSST)
                if _char_sync and _srv_rage is not None:
                    _char_sync.rage = _srv_rage
                if _char_sync and _srv_mana is not None:
                    _char_sync.mana = _srv_mana   # CharacterStats.mana — display e _check_mana cliente
                if cs and _srv_mana is not None:
                    cs.mana = _srv_mana            # CombatStats.mana — checks client-side
                if _char_sync and _srv_conc is not None:
                    _char_sync.concentration = _srv_conc
            # Restauração de mana (consumível instantâneo ou HoT tick)
            _mana_amt = payload.get("mana_amount", 0)
            if _mana_amt > 0:
                from components import Position as _PosMR
                _pos_mr = self.world.get_component(self.player_entity, _PosMR)
                if _pos_mr:
                    from floating_text import FLT as _FLT_mr
                    _FLT_mr.add(f"+{_mana_amt} MP", _pos_mr.x, _pos_mr.y - 28,
                                (100, 180, 255), size="normal", target_id=self.player_entity)

            # Cura própria (skill, consumível HoT).
            # O servidor envia hp=valor_no_momento_da_cura. Como HP5 regen e outros
            # heals podem ocorrer no mesmo tick (mas com hp_after mais recente no
            # AOI_UPDATE), aplicamos: max(current, payload_hp) para nunca regredir.
            _heal_amt = payload.get("heal_amount", 0)
            if _heal_amt > 0 and cs:
                if cs.max_hp > 0 and payload.get("hp_max", 0) > 0:
                    cs.max_hp = payload["hp_max"]
                _hp_from_srv = payload.get("hp", 0)
                if _hp_from_srv > 0:
                    # Mantém o maior entre o HP atual do cliente (já pode incluir
                    # HP5 regen do AOI_UPDATE) e o HP do servidor neste evento
                    cs.current_hp = min(cs.max_hp, max(cs.current_hp, _hp_from_srv))
                from floating_text import FLT as _FLT_heal
                from components import Position as _PosHeal
                _pos_h = self.world.get_component(self.player_entity, _PosHeal)
                if _pos_h:
                    _FLT_heal.add(f"+{_heal_amt} HP", _pos_h.x, _pos_h.y - 20,
                                  (100, 255, 120), size="normal",
                                  target_id=self.player_entity)
            # XP ganho (notificação do servidor — XP proporcional por dano)
            xp_gained = payload.get("xp_gained", 0)
            if xp_gained > 0:
                from components import CharacterStats, Position, PermanentStats
                from stats_system import process_levelups
                char_stats = self.world.get_component(self.player_entity, CharacterStats)
                cs_xp      = self.world.get_component(self.player_entity, CombatStats)
                perm_xp    = self.world.get_component(self.player_entity, PermanentStats)
                if char_stats:
                    char_stats.current_xp += xp_gained
                    # Servidor é autoritativo para pontos de talento — não dá localmente
                    process_levelups(self.world, self.player_entity,
                                     char_stats, cs_xp, perm_xp,
                                     give_talent_points=False)
                from floating_text import FLT
                pos = self.world.get_component(self.player_entity, Position)
                if pos:
                    FLT.add(f"+{xp_gained} XP", pos.x, pos.y - 20, (100, 255, 100), size="small",
                            target_id=self.player_entity)
            # Level-up: HP e pontos de talento autoritativos do servidor
            _srv_tp = payload.get("talent_points")
            if _srv_tp is not None:
                from components import TalentTree as _TTsync
                _tt_s = self.world.get_component(self.player_entity, _TTsync)
                if _tt_s:
                    _tt_s.available_points = int(_srv_tp)
            if "hp" in payload and "heal_amount" not in payload and cs:
                cs.current_hp = payload["hp"]
                if payload.get("hp_max", 0) > 0:
                    cs.max_hp = payload["hp_max"]
            # on_kill charge: servidor confirmou carga da skill (ex: Vitória Iminente)
            _on_kill_sid = payload.get("on_kill_skill")
            if _on_kill_sid:
                _ps_vi = self.world.get_component(self.player_entity, PlayerSkills)
                if _ps_vi:
                    for _sk_vi in _ps_vi.skills:
                        if _sk_vi and _sk_vi.skill_id == _on_kill_sid:
                            if _sk_vi.charges < _sk_vi.max_charges:
                                _sk_vi.charges      = _sk_vi.max_charges
                                _sk_vi.charge_timer = _sk_vi.charge_timeout
                            from floating_text import WARN
                            WARN.add("Vitória Iminente!")
                            break
        # PvP: aplica efeitos recebidos pelo próprio jogador (vítima)
        if eid == self._my_eid and payload.get("applied_effects"):
            from core_systems import apply_effect as _ae_pvp
            _ae_pvp_durs = payload.get("effect_durations", {})
            for _ae_pvp_ef in payload["applied_effects"]:
                _ae_pvp_dur = _ae_pvp_durs.get(_ae_pvp_ef, 5.0)
                _ae_pvp(self.world, self.player_entity, _ae_pvp_ef, _ae_pvp_dur)
                # Para movimento imediatamente ao receber CC via PvP
                if _ae_pvp_ef in ("root", "stun", "polymorph", "disoriented"):
                    from components import CombatState as _CStAE, TileMovement as _TMAE
                    from components import PlayerAutoMove as _PAMAE
                    _cst_ae = self.world.get_component(self.player_entity, _CStAE)
                    _tm_ae  = self.world.get_component(self.player_entity, _TMAE)
                    _am_ae  = self.world.get_component(self.player_entity, _PAMAE)
                    if _ae_pvp_ef == "root" and _cst_ae:
                        # is_rooted será mantido/resetado por StatusEffectSystem via sfx.has("root")
                        _cst_ae.is_rooted   = True
                        _cst_ae.is_pursuing = False
                    elif _ae_pvp_ef == "stun" and _cst_ae:
                        # Stun requer timer para CombatStateSystem poder resetar
                        _cst_ae.is_stunned  = True
                        _cst_ae.stun_timer  = _ae_pvp_dur
                        _cst_ae.is_pursuing = False
                    elif _ae_pvp_ef in ("polymorph", "disoriented") and _cst_ae:
                        # NÃO seta is_stunned — PlayerInputSystem verifica
                        # StatusEffects.has("polymorph/disoriented") diretamente.
                        # Setar is_stunned sem stun_timer deixa is_stunned=True para sempre
                        # quando polymorph é quebrado por dano (sem passar por CombatStateSystem).
                        _cst_ae.is_pursuing = False
                    # Para a animação de tile atual
                    if _tm_ae and _tm_ae.is_moving:
                        _tm_ae.is_moving      = False
                        _tm_ae.progress       = 0.0
                        _tm_ae.current_tile_x = _tm_ae.target_tile_x
                        _tm_ae.current_tile_y = _tm_ae.target_tile_y
                    # Limpa pursuit e path
                    if _am_ae:
                        _am_ae.active = False
                        _am_ae.path.clear()

        elif eid in self._remote_players:
            local_eid = self._remote_players[eid]
            rc = self.world.get_component(local_eid, RemoteControlled)
            if rc:
                if "hp" in payload:
                    rc.hp = payload["hp"]
                if "hp_max" in payload:
                    rc.hp_max = payload["hp_max"]
        elif eid in self._remote_mobs:
            _meta_ehp = self._meta(eid)
            if _meta_ehp and "hp" in payload:
                _meta_ehp.hp = payload["hp"]
                if "hp_max" in payload:
                    _meta_ehp.hp_max = payload["hp_max"]

    def _handle_msg_player_death(self, payload: dict) -> None:
        # Servidor declarou que o player local morreu.
        from components import CombatStats, CombatState, CharacterStats as _CHS_d
        cs      = self.world.get_component(self.player_entity, CombatStats)
        char_d  = self.world.get_component(self.player_entity, _CHS_d)
        if cs:
            cs.current_hp = 0   # DeathRespawnSystem detecta e respawna
            # Restaura HP autoritativo do servidor após respawn
            _srv_hp      = payload.get("hp",     0)
            _srv_hp_max  = payload.get("hp_max", 0)
            if _srv_hp_max > 0:
                cs.max_hp     = _srv_hp_max
                cs.current_hp = _srv_hp
        # Restaura mana cheia (servidor já restaurou server-side)
        if char_d:
            _srv_mana     = payload.get("mana",     0)
            _srv_max_mana = payload.get("max_mana", 0)
            if _srv_max_mana > 0:
                char_d.mana     = _srv_mana
                char_d.max_mana = _srv_max_mana
            if cs:
                cs.mana = char_d.mana   # sincroniza CombatStats.mana também
        # Teleporta para o ponto de respawn (servidor envia coords)
        _rx = payload.get("respawn_tx", 0)
        _ry = payload.get("respawn_ty", 0)
        if _rx and _ry:
            from components import Position as _PosD
            _ptm = self.world.get_component(self.player_entity, TileMovement)
            _ppo = self.world.get_component(self.player_entity, _PosD)
            if _ptm:
                _ptm.current_tile_x = _rx; _ptm.current_tile_y = _ry
                _ptm.target_tile_x  = _rx; _ptm.target_tile_y  = _ry
                _ptm.is_moving = False;     _ptm.progress = 0.0
            if _ppo:
                _ppo.x = _rx * TILE_SIZE + TILE_SIZE // 2
                _ppo.y = _ry * TILE_SIZE + TILE_SIZE // 2
        # Para de atacar
        combat_state = self.world.get_component(self.player_entity, CombatState)
        if combat_state:
            combat_state.target_entity_id = -1
            combat_state.is_pursuing      = False
        self._net_last_target = -1

    def _handle_msg_loot_available(self, payload: dict) -> None:
        # Servidor concedeu loot ao player local.
        # Cria entidade Corpse no ECS local para o LootSystem offline
        # funcionar IDENTICAMENTE ao offline (modal, equip, coins, scroll).
        from entity_factory import create_corpse
        from loot_tables import _T
        from tileset import TILE_SIZE as _TS
        corpse_id = payload.get("corpse_id", -1)
        coins     = payload.get("coins", 0)
        tx        = payload.get("tx", 0)
        ty        = payload.get("ty", 0)
        if corpse_id < 0:
            return
        # Reconstrói objetos de item a partir dos dados serializados do servidor
        loot_items = []
        for item_data in payload.get("items", []):
            item_name = item_data.get("name", "")
            for _key, factory in _T.items():
                try:
                    candidate = factory()
                except Exception:
                    continue
                if getattr(candidate, "name", "") == item_name:
                    loot_items.append(candidate)
                    break
        # Cria entidade Corpse no ECS local — LootSystem offline lê daqui
        px = tx * _TS + _TS // 2
        py = ty * _TS + _TS // 2
        local_corpse_eid = create_corpse(self.world, px, py, loot_items, coins,
                                         decay_time=120.0)
        # Guarda mapeamento corpse_id (servidor) → local ECS eid
        self._available_loot[corpse_id] = {
            "local_eid": local_corpse_eid, "tx": tx, "ty": ty
        }
        # Também mantém no _remote_corpses para renderização pelos outros players
        self._remote_corpses[corpse_id] = (tx, ty)

    def _handle_msg_sound_event(self, payload: dict) -> None:
        _ev_kind  = payload.get("kind", "")
        _ev_mob   = payload.get("mob_name", "")
        _ev_seid  = payload.get("mob_eid", -1)
        _ev_tx    = payload.get("tx", 0)
        _ev_ty    = payload.get("ty", 0)
        from tileset import TILE_SIZE as _TS_snd
        _ev_sx = _ev_tx * _TS_snd + _TS_snd // 2
        _ev_sy = _ev_ty * _TS_snd + _TS_snd // 2
        _elx, _ely = self._player_world_pos()
        if _ev_kind == "mob_aggro":
            # Usa MobSounds component se o mob estiver no AOI do cliente
            _ev_local = self._remote_mobs.get(_ev_seid)
            _ev_snd   = None
            if _ev_local is not None:
                from components import MobSounds as _MSev
                _ev_snd = self.world.get_component(_ev_local, _MSev)
            SOUNDS.play_mob_sounds_at(_ev_snd, "aggro",
                                      _ev_sx, _ev_sy, _elx, _ely, base=0.8,
                                      dedup_key=f"aggro_{_ev_seid}")

    def _handle_msg_loot_result(self, payload: dict) -> None:
        # Servidor confirmou o loot. O LootSystem offline já processou os itens
        # localmente via entidade Corpse criada em LOOT_AVAILABLE.
        # Aqui apenas garantimos limpeza caso o corpo ainda exista.
        corpse_id = payload.get("corpse_id", -1)
        loot_data = self._available_loot.pop(corpse_id, None)
        if loot_data:
            local_eid = loot_data.get("local_eid")
            if local_eid is not None:
                try:
                    self.world.remove_entity(local_eid)
                except Exception:
                    pass
        self._remote_corpses.pop(corpse_id, None)
        # Gold/itens mudaram — sincroniza save com o servidor
        self._send_save_state()

    def _handle_msg_buy_result(self, payload: dict) -> None:
        # Servidor validou a compra — aplica localmente se sucesso
        if payload.get("success"):
            from components import Inventory as _InvBR, Wallet as _WalBR
            inv_br = self.world.get_component(self.player_entity, _InvBR)
            wal_br = self.world.get_component(self.player_entity, _WalBR)
            # Atualiza gold (servidor é autoritativo)
            new_gold = payload.get("new_gold", 0)
            if wal_br is not None:
                wal_br.gold = new_gold
            # Adiciona item ao inventário local
            item_data = payload.get("item", {})
            if inv_br and item_data:
                # _item_from_data: dados completos vêm do servidor — não precisa de catálogo
                item_br = self._item_from_data(item_data)
                if item_br:
                    qty_br = payload.get("quantity", 1)
                    # Tenta empilhar
                    stacked = False
                    if getattr(item_br, "max_stack", 1) > 1:
                        for ex_br in inv_br.items:
                            if ex_br and ex_br.name == item_br.name and \
                                    ex_br.stack < ex_br.max_stack:
                                ex_br.stack = min(ex_br.max_stack,
                                                  ex_br.stack + qty_br)
                                stacked = True
                                break
                    if not stacked and len(inv_br.items) < inv_br.max_slots:
                        item_br.stack = qty_br
                        inv_br.items.append(item_br)
            from combat_log import LOG as _LOG_BR
            price = payload.get("price", 0)
            name  = item_data.get("name", "item")
            _LOG_BR.add(f"Comprado: {name} por {price}g", (255, 215, 0))
            # Salva imediatamente — inventário e gold foram alterados server-side
            self._send_save_state()
        else:
            reason = payload.get("reason", "")
            _reason_msg = {
                "insufficient_gold": "Ouro insuficiente",
                "inventory_full":    "Inventário cheio",
                "item_not_in_stock": "Item não disponível",
                "invalid_shop":      "Loja inválida",
            }.get(reason, "Compra recusada")
            from floating_text import WARN as _WARN_BR
            _WARN_BR.add(_reason_msg)

    def _handle_msg_sell_result(self, payload: dict) -> None:
        from components import Wallet as _WalSR
        wal_sr = self.world.get_component(self.player_entity, _WalSR)
        if payload.get("success"):
            # Servidor confirma — atualiza gold autoritativo
            if wal_sr is not None:
                wal_sr.gold = payload.get("new_gold", wal_sr.gold)
            from combat_log import LOG as _LOG_SR
            _LOG_SR.add(
                f"Vendido: {payload.get('item_name','item')} por {payload.get('sell_price',0)}g",
                (180, 220, 100))
            self._send_save_state()
        else:
            # Falha rara (sessão inválida etc.) — reverte gold local se servidor informou
            # o valor correto
            srv_gold = payload.get("new_gold")
            if srv_gold is not None and wal_sr is not None:
                wal_sr.gold = srv_gold
            from floating_text import WARN as _WARN_SR
            _WARN_SR.add(payload.get("reason", "Venda recusada"))

    def _handle_msg_pong(self, payload: dict) -> None:
        if self._net:
            rtt = int(__import__("time").time() * 1000) - payload.get("client_ts", 0)
            self._net.latency_ms = rtt
