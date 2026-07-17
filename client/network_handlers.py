"""
network_handlers.py — Mixin com os handlers de mensagens de rede do cliente.
Separado de game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.world, self._my_eid, self._net e os demais atributos referenciados aqui.
"""
import random
from engine.components import PlayerSkills, TileMovement
from engine.tileset import TILE_SIZE
from ui.combat_log import LOG
from ui.floating_text import PROC
from ui.sound_manager import SOUNDS


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
        elif msg_type == MsgType.SKILL_EFFECT:
            self._handle_msg_skill_effect(payload)
        elif msg_type == MsgType.ENTITY_DESPAWN:
            self._handle_msg_entity_despawn(payload)
        elif msg_type == MsgType.ENTITY_MOVE:
            self._handle_msg_entity_move(payload)
        elif msg_type == MsgType.AOI_UPDATE:
            self._handle_msg_aoi_update(payload)
        elif msg_type == MsgType.STATS_UPDATE:
            self._handle_msg_stats_update(payload)
        elif msg_type == MsgType.SKILL_LEVELS_UPDATE:
            self._handle_msg_skill_levels_update(payload)
        elif msg_type == MsgType.QUEST_UPDATE:
            self._handle_msg_quest_update(payload)
        elif msg_type == MsgType.PLAYER_DEATH:
            self._handle_msg_player_death(payload)
        elif msg_type == MsgType.PLAYER_REVIVE:
            self._handle_msg_player_revive(payload)
        elif msg_type == MsgType.GHOST_STATE:
            self._handle_msg_ghost_state(payload)
        elif msg_type == MsgType.ENTITY_DEATH:
            self._handle_msg_entity_death(payload)
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
        elif msg_type == MsgType.ZONE_CHANGE:
            self._handle_msg_zone_change(payload)
        elif msg_type == MsgType.EQUIP_REJECTED:
            self._handle_msg_equip_rejected(payload)
        elif msg_type == MsgType.TRADE_INVITE:
            self._handle_msg_trade_invite(payload)
        elif msg_type == MsgType.TRADE_OPEN:
            self._handle_msg_trade_open(payload)
        elif msg_type == MsgType.TRADE_STATE:
            self._handle_msg_trade_state(payload)
        elif msg_type == MsgType.TRADE_RESULT:
            self._handle_msg_trade_result(payload)
        elif msg_type == MsgType.TRADE_CANCELLED:
            self._handle_msg_trade_cancelled(payload)
        elif msg_type == MsgType.DUEL_INVITE:
            self._handle_msg_duel_invite(payload)
        elif msg_type == MsgType.DUEL_START:
            self._handle_msg_duel_start(payload)
        elif msg_type == MsgType.DUEL_END:
            self._handle_msg_duel_end(payload)
        elif msg_type == MsgType.CHAT_MESSAGE:
            self._handle_msg_chat_message(payload)


    def _handle_msg_login_ok(self, payload: dict) -> None:
        from engine.components import TileMovement
        self._my_eid = payload.get("eid", -1)
        char = payload.get("char", {})
        tx   = int(char.get("tile_x", 10))
        ty   = int(char.get("tile_y", 10))
        # Sincroniza posição local com o servidor — evita conflito com save offline
        from engine.components import Position
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
        from engine.components import CombatStats, CharacterStats as _CS_login
        from engine.stats_system import apply_char_stats_to_combat, sync_attack_interval, process_levelups, CLASS_BASE_STATS
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
            # Nome real do personagem (coluna `name` do banco, sempre presente
            # no payload "char") — sem isso, CharacterStats.name nunca saía do
            # default "Aventureiro" (só o SERVIDOR aplicava o nome real na sua
            # própria cópia, via WorldServer.spawn_player; a cópia LOCAL do
            # próprio player nunca recebia) — nameplate do próprio personagem
            # sempre mostrava "Aventureiro" em vez do nome de verdade. Bug
            # relatado pelo usuário 15/07/2026.
            char_stat.name = char.get("name") or char_stat.name
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
                # Spirit NÃO está em CLASS_BASE_STATS (não cresce com level/
                # classe, só item/talento futuro) — default literal 0.
                char_stat.spirit           = int(_stats_s.get("spirit", 0))
                _gold_s = int(_stats_s.get("gold", 0))
                if _gold_s > 0:
                    from engine.components import Wallet as _W_login
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
                char_stat.spirit       = 0

            # SEMPRE recalcula CombatStats — garante CLASS_MELEE_OVERRIDES aplicado
            from engine.components import PermanentStats as _PS_login
            from engine.components import Equipment as _EqLogin
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
        # Carrega o mapa correto se o personagem estava em outro mapa ao deslogar.
        # Mirrors _restore_save_state do offline: checa map_id e chama _do_transition.
        # Checa .endswith(".csv") pra ignorar valores legados inválidos (ex: "map_main").
        _saved_map = char.get("map_id", "")
        if _saved_map and _saved_map.endswith(".csv") and _saved_map != self._current_map_file:
            self._do_transition({"target_map": _saved_map, "target_x": tx, "target_y": ty})

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
                    "kind":     kind,
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
                "kind":     kind,
            })

    def _handle_msg_combat_result(self, payload: dict) -> None:
        self._apply_combat_result(payload)

    def _handle_msg_skill_result(self, payload: dict) -> None:
        caster_eid = payload.get("caster_eid", -1)
        sid        = payload.get("sid", "")
        targets    = payload.get("targets", [])
        # Apenas gameplay — som/VFX chegam via SKILL_EFFECT separado
        if caster_eid == self._my_eid:
            _failed_sr     = payload.get("failed",        False)
            _ps_sr         = self.world.get_component(self.player_entity, PlayerSkills)
            _cast_started  = payload.get("cast_started",  False)
            _is_completion = payload.get("is_completion", False)
            _is_proj_dmg   = payload.get("is_proj_damage", False)
            if _failed_sr:
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
                from engine.components import SpellCast as _SCFail, CombatState as _CSFail
                _sc_fail = self.world.get_component(self.player_entity, _SCFail)
                if _sc_fail and _sc_fail.spell_id == sid:
                    self.world.remove_component(self.player_entity, _SCFail)
                _cs_fail = self.world.get_component(self.player_entity, _CSFail)
                if _cs_fail:
                    _cs_fail.is_casting = False
                _fail_reason = payload.get("reason", "")
                if _fail_reason:
                    from ui.floating_text import WARN as _WARN_fail
                    _WARN_fail.add(_fail_reason)
                    # "Alvo amigável" (server/skill_processor.py): o cast
                    # nunca vai completar contra esse alvo — sem limpar
                    # is_pursuing aqui, o personagem fica perseguindo o NPC
                    # pra sempre (nenhum outro código limpa is_pursuing numa
                    # falha, só num cast bem-sucedido). Bug real relatado
                    # pelo usuário 17/07/2026 (arqueiro travado tentando
                    # alcançar o Guarda Real). O gate client-side em
                    # ui/systems.py::_use_skill_visual_only já evita a
                    # maioria dos casos — isto é rede de segurança.
                    if _fail_reason == "Alvo amigável" and _cs_fail:
                        _cs_fail.is_pursuing = False
            elif _cast_started:
                self._cancelled_spell_ids.discard(sid)
                if _ps_sr:
                    _ps_sr.gcd_timer = PlayerSkills.GCD_DURATION
                    for _sk_sr in _ps_sr.skills:
                        if _sk_sr and _sk_sr.skill_id == sid:
                            _sk_sr._server_pending         = False
                            _sk_sr._server_pending_timeout = 0.0
            elif _is_completion:
                if _ps_sr:
                    _srv_cd = payload.get("cooldown")
                    for _sk_sr in _ps_sr.skills:
                        if _sk_sr and _sk_sr.skill_id == sid:
                            _sk_sr._server_pending         = False
                            _sk_sr._server_pending_timeout = 0.0
                            _sk_sr.current_cooldown = float(_srv_cd) if _srv_cd is not None else _sk_sr.cooldown
            elif _is_proj_dmg:
                pass
            else:
                # Skill instantânea: GCD + cooldown
                if _ps_sr:
                    _ps_sr.gcd_timer = PlayerSkills.GCD_DURATION
                    _srv_cd = payload.get("cooldown")
                    for _sk_sr in _ps_sr.skills:
                        if _sk_sr and _sk_sr.skill_id == sid:
                            _sk_sr._server_pending         = False
                            _sk_sr._server_pending_timeout = 0.0
                            _sk_sr.current_cooldown = float(_srv_cd) if _srv_cd is not None else _sk_sr.cooldown
        # Escudo de Fogo confirmado: adiciona FireShieldEffect no cliente para visual + timer
        if caster_eid == self._my_eid and sid == "escudo_fogo" and not payload.get("failed"):
            from engine.components import FireShieldEffect as _FSEcl
            if not self.world.get_component(self.player_entity, _FSEcl):
                self.world.add_component(self.player_entity, _FSEcl(duration=15.0))

        # Bloco de Gelo confirmado: adiciona IceBlockEffect no cliente.
        # Sem isso, IceBlockSystem nunca roda online e a cura (10%/s) nunca
        # aparece no HP bar — o jogador só vê a cura toda de uma vez quando
        # o servidor sincroniza o HP por outro motivo.
        if caster_eid == self._my_eid and sid == "bloco_de_gelo" and not payload.get("failed"):
            from engine.components import IceBlockEffect as _IBEcl, CombatState as _CStIB
            if not self.world.get_component(self.player_entity, _IBEcl):
                self.world.add_component(self.player_entity, _IBEcl(
                    duration=5.0, elapsed=0.0, heal_interval=1.0, last_heal=0.0,
                ))
                _cst_ib = self.world.get_component(self.player_entity, _CStIB)
                if _cst_ib:
                    _cst_ib.is_stunned = True
                    _cst_ib.is_immune  = True

        # Camuflagem confirmada: aplica localmente (servidor só roda o handler
        # no _skill_system dele — o cliente nunca chama _skill_camuflagem
        # diretamente, ver _use_skill_visual_only). Sem isso, o cliente nunca
        # saberia da velocidade reduzida / disfarce, já que isso não é
        # client-predicted como Interceptar.
        if caster_eid == self._my_eid and sid == "camuflagem" and not payload.get("failed"):
            import random as _rand_cam
            from engine.components import (CombatStats as _CSCam, CombatState as _CStCam,
                                    TileMovement as _TMCam, StatusEffects as _SFXCam)
            from content.skill_config import SKILL_CATALOG as _SCCam
            from engine.tileset import discover_camouflage_variants as _disc_cam
            _params_cam = _SCCam.get("camuflagem", {}).get("params", {})
            _dur_cam    = _params_cam.get("duration",  5.0)
            _spd_cam    = _params_cam.get("speed_pct", 0.60)
            _cs_cam     = self.world.get_component(self.player_entity, _CSCam)
            _cst_cam    = self.world.get_component(self.player_entity, _CStCam)
            _tm_cam     = self.world.get_component(self.player_entity, _TMCam)
            if _cs_cam:
                _cs_cam.camouflage_timer  = _dur_cam
                _variants_cam = _disc_cam()
                _cs_cam.camouflage_object = _rand_cam.choice(_variants_cam) if _variants_cam else ""
            if _cst_cam:
                _cst_cam.is_visible    = False
                _cst_cam.is_immune     = True
                _cst_cam.is_camouflaged = True
            if _tm_cam:
                _tm_cam.speed = 110.0 * _spd_cam
            _sfx_cam = self.world.get_component(self.player_entity, _SFXCam)
            if _sfx_cam:
                for _dot_cam in ("poison", "bleed", "burn"):
                    _sfx_cam.remove(_dot_cam)

        # Consome carga livre de Executar ao usar a skill
        if caster_eid == self._my_eid and sid == "executar":
            from engine.components import CharacterStats as _CSexec
            _char_exec = self.world.get_component(self.player_entity, _CSexec)
            if _char_exec and _char_exec.free_executar_charges > 0:
                _char_exec.free_executar_charges -= 1

        # Procs sincronizados pelo servidor
        if caster_eid == self._my_eid and payload.get("assassino_proc"):
            from engine.components import CharacterStats as _CSproc
            _char_proc = self.world.get_component(self.player_entity, _CSproc)
            if _char_proc:
                _char_proc.free_executar_charges = 1
                from ui.combat_log import LOG as _LOG_proc
                _LOG_proc.add("Assassino: Executar disponivel! (sem custo, sem restricao de HP)",
                              (255, 80, 80))
                PROC.add("Assassino!", (255, 80, 80))

        if caster_eid == self._my_eid and payload.get("fire_instant_proc"):
            from engine.components import CharacterStats as _CSfi
            _char_fi = self.world.get_component(self.player_entity, _CSfi)
            if _char_fi:
                _char_fi.fire_instant_ready = True
                from ui.combat_log import LOG as _LOG_fi
                _LOG_fi.add("Chama Interna: proxima Bola de Fogo instantanea e gratis!",
                            (255, 160, 60))
                PROC.add("Chama Interna!", (255, 160, 60))

        if caster_eid == self._my_eid and payload.get("lapso_proc"):
            _lp = payload["lapso_proc"]
            _lp_bonus = float(_lp.get("bonus", 0.0))
            _lp_dur   = float(_lp.get("duration", 5.0))
            if _lp_bonus > 0:
                from engine.components import CombatStats as _CSlp
                from engine.components import Modifier as _Modlp
                from engine.stat_fns import add_timed_modifier as _atm_lp
                _cs_lp = self.world.get_component(self.player_entity, _CSlp)
                if _cs_lp:
                    _atm_lp(_cs_lp, _Modlp("crit_rating", _lp_bonus, "flat", source="buff"),
                            _lp_dur, "lapso_elemental")
                from ui.combat_log import LOG as _LOG_lp
                _LOG_lp.add(f"Lapso Elemental: +{int(_lp_bonus*100)}% Critico por {_lp_dur:.0f}s!",
                            (255, 180, 50))
                PROC.add("Lapso Elemental!", (255, 180, 50))

        # BdF is_completion: servidor envia projectile_target → cria projétil aqui.
        # is_proj_damage: dano confirmado após PROJECTILE_HIT_CS → mostra números.
        _bdf_deferred: set = set()
        if caster_eid == self._my_eid and sid == "bola_de_fogo":
            from engine.components import PlayerProjectile as _PPcomp, Position as _PPpos2
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
                    # Quest "use_skill": só conta aqui, quando o dano do projétil já foi
                    # confirmado pelo servidor — não na ativação da skill (cast iniciado),
                    # ver _use_skill_visual_only em systems.py. on_dummy checa o proxy
                    # local do alvo (mob remoto), igual ao resto do client.
                    if t.get("damage", 0) > 0:
                        from engine.quest_events import fire as _quest_fire_pdmg
                        from engine.components import TrainingDummy as _TDpdmg
                        _t_local_pdmg = self._remote_mobs.get(_t_srv)
                        _on_dummy_pdmg = (_t_local_pdmg is not None
                                         and self.world.get_component(_t_local_pdmg, _TDpdmg) is not None)
                        _quest_fire_pdmg("use_skill", skill_id=sid, on_dummy=_on_dummy_pdmg)

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
                "is_proj_damage": payload.get("is_proj_damage", False),
            }
            if "mob_slow_mult" in t:
                _cr_t["mob_slow_mult"] = t["mob_slow_mult"]
            self._apply_combat_result(_cr_t)
        # LOG e aplicação local de efeitos confirmados pelo servidor
        if caster_eid == self._my_eid:
            from content.status_effects_data import EFFECT_DEFS as _EDEFS_sr
            from engine.core_systems import apply_effect as _ae_apply
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
                    from engine.components import EntityIdentity as _EI_sr, RemoteControlled as _RCae
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
            from engine.components import PlayerProjectile as _PPfr
            from engine.components import Position as _PosFR
            from content.skill_config import SKILL_CATALOG as _SC_fr2
            _fr_params  = _SC_fr2.get("flecha_reiterada", {}).get("params", {})
            _fr_delay   = _fr_params.get("arrow_delay", 0.25)
            _fr_ap_mult = _fr_params.get("damage_multiplier", 2.0)
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

        # Picada de Escorpião is_completion: cria projétil visual.
        # Primeira (única) flecha envia PROJECTILE_HIT_CS ao colidir.
        _pe_srv_tgt = payload.get("projectile_target", -1)
        if (caster_eid == self._my_eid
                and sid == "picada_escorpiao"
                and payload.get("is_completion")
                and not payload.get("failed")
                and sid not in self._cancelled_spell_ids
                and _pe_srv_tgt != -1):
            from engine.components import PlayerProjectile as _PPpe
            from engine.components import Position as _PosPS
            from content.skill_config import SKILL_CATALOG as _SC_pe
            _pe_ap = _SC_pe.get("picada_escorpiao", {}).get("params", {}).get("damage_multiplier", 1.5)
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
                        color            = (60, 200, 80),
                        damage_type      = "physical",
                        ap_multiplier    = _pe_ap,
                        guaranteed_hit   = True,
                        target_last_x    = _pe_tlx,
                        target_last_y    = _pe_tly,
                        target_server_id = _pe_srv_tgt,
                    ))

        # Tiro Repulsivo is_completion: cria projétil visual.
        # Knockback/stun aplicados pelo servidor após PROJECTILE_HIT_CS.
        _tr_srv_tgt = payload.get("projectile_target", -1)
        if (caster_eid == self._my_eid
                and sid == "tiro_repulsivo"
                and payload.get("is_completion")
                and not payload.get("failed")
                and sid not in self._cancelled_spell_ids
                and _tr_srv_tgt != -1):
            from engine.components import PlayerProjectile as _PPtrep
            from engine.components import Position as _PosTR
            from content.skill_config import SKILL_CATALOG as _SC_tr
            _tr_ap = _SC_tr.get("tiro_repulsivo", {}).get("params", {}).get("damage_multiplier", 1.5)
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

        # Tiro Múltiplo is_completion: 1 flecha por alvo selecionado pelo servidor
        # (cone de visão + range + talento, ver _complete_tiro_multiplo_cast) — não
        # mais dano instantâneo de área. Cada flecha tem seu próprio target_server_id
        # e envia PROJECTILE_HIT_CS independente ao colidir, igual às outras skills
        # de flecha (mesma mecânica do projeto offline: 1 PlayerProjectile por alvo).
        _tm_srv_tgts = payload.get("projectile_targets", [])
        if (caster_eid == self._my_eid
                and sid == "tiro_multiplo"
                and payload.get("is_completion")
                and not payload.get("failed")
                and sid not in self._cancelled_spell_ids
                and _tm_srv_tgts):
            from engine.components import PlayerProjectile as _PPtm
            from engine.components import Position as _PosTM
            from content.skill_config import SKILL_CATALOG as _SC_tm
            _tm_ap = _SC_tm.get("tiro_multiplo", {}).get("params", {}).get("damage_multiplier", 3.0)
            _pl_pos_tm = self.world.get_component(self.player_entity, _PosTM)
            for _tm_idx, _tm_srv_tgt in enumerate(_tm_srv_tgts):
                _tm_loc = self._remote_mobs.get(
                    _tm_srv_tgt, self._remote_players.get(_tm_srv_tgt, -1))
                if _tm_loc == -1 or not _pl_pos_tm:
                    continue
                _tgt_pos_tm = self.world.get_component(_tm_loc, _PosTM)
                _tm_tlx = _tgt_pos_tm.x if _tgt_pos_tm else _pl_pos_tm.x
                _tm_tly = _tgt_pos_tm.y if _tgt_pos_tm else _pl_pos_tm.y
                _peid_tm = self.world.create_entity()
                self.world.add_component(_peid_tm, _PosTM(
                    x=_pl_pos_tm.x, y=_pl_pos_tm.y,
                    prev_x=_pl_pos_tm.x, prev_y=_pl_pos_tm.y,
                ))
                self.world.add_component(_peid_tm, _PPtm(
                    spell_id         = "tiro_multiplo",
                    attacker_id      = self.player_entity,
                    target_id        = _tm_loc,
                    speed            = 700.0,
                    dmg_weapon_pct   = 1.0,
                    dmg_sp_coeff     = 0.0,
                    color            = (150, 210, 255),
                    damage_type      = "physical",
                    launch_delay     = _tm_idx * 0.06,  # leve escalonamento visual
                    ap_multiplier    = _tm_ap,
                    guaranteed_hit   = True,
                    target_last_x    = _tm_tlx,
                    target_last_y    = _tm_tly,
                    target_server_id = _tm_srv_tgt,
                ))

    # Skills de projétil cujo espectador (player remoto) precisa de uma flecha/bola
    # cosmética ao receber SKILL_EFFECT{event:"launch"} — ver _spawn_bystander_projectile.
    _BYSTANDER_PROJECTILE_SKILLS = {"bola_de_fogo", "flecha_reiterada",
                                     "picada_escorpiao", "tiro_repulsivo", "tiro_multiplo"}

    def _handle_msg_skill_effect(self, payload: dict) -> None:
        """Handler de SKILL_EFFECT — apresentação (som/VFX) + projétil cosmético de
        espectador no evento "launch". Broadcast AOI automático (qualquer player
        próximo recebe, sem canal/loop manual por skill — ver session.py
        consume_skill_effects), diferente do antigo proj_incoming (canal per-player
        sem filtro de AOI, com allowlist de campos fácil de esquecer ao adicionar
        skill nova — causa raiz do bug "flecha não aparece pro remoto"). Ver
        arquitetura/PROBLEMAS_ARQUITETURA.md."""
        from content.skill_config import SKILL_CATALOG as _SC_sfx
        sid        = payload.get("sid", "")
        event      = payload.get("event", "")
        caster_eid = payload.get("caster_eid", -1)
        if not sid or not event:
            return
        if sid == "tiro_repulsivo" and event == "collision":
            self._show_knockback_collision(payload)
            return
        is_local = caster_eid == self._my_eid
        # Projétil cosmético: independente de som — a skill pode não ter "launch"
        # configurado em SKILL_CATALOG (ex: Tiro Múltiplo) e ainda assim precisa
        # da flecha visual pro espectador. Caster cria a sua própria via SKILL_RESULT
        # (is_completion) — aqui é só quem está assistindo.
        if event == "launch" and not is_local and sid in self._BYSTANDER_PROJECTILE_SKILLS:
            self._spawn_bystander_projectile(sid, payload)

        fx = _SC_sfx.get(sid, {}).get("effects", {}).get(event, {})
        snd  = fx.get("sound")   # som único → play_skill
        snds = fx.get("sounds")  # variações aleatórias → play_random
        if not snd and not snds:
            return
        if is_local:
            if snd:
                SOUNDS.play_skill(snd)
            else:
                SOUNDS.play_random(snds)
        else:
            # Som posicional a partir das coords de tile do payload
            from engine.tileset import TILE_SIZE as _TS_sfx
            _sfx_wx = payload.get("tx", 0) * _TS_sfx + _TS_sfx // 2
            _sfx_wy = payload.get("ty", 0) * _TS_sfx + _TS_sfx // 2
            _slx, _sly = self._player_world_pos()
            if snd:
                SOUNDS.play_skill_at(snd, _sfx_wx, _sfx_wy, _slx, _sly, base=0.85)
            else:
                SOUNDS.play_random_at(snds, _sfx_wx, _sfx_wy, _slx, _sly, base=0.85)

    def _resolve_local_eid(self, server_eid: int) -> int:
        """Mob remoto, player remoto, ou eu mesmo — local eid do server_eid dado,
        ou -1 se nenhum (ex: alvo fora do AOI deste cliente)."""
        if server_eid == self._my_eid:
            return self.player_entity
        if server_eid in self._remote_players:
            return self._remote_players[server_eid]
        return self._remote_mobs.get(server_eid, -1)

    def _spawn_bystander_projectile(self, sid: str, payload: dict) -> None:
        """Cria a flecha/bola cosmética que um espectador vê quando OUTRO player
        lança uma skill de projétil. Nunca envia PROJECTILE_HIT_CS (target_server_id
        = -2) — o dano desse caster já chega normalmente via SKILL_RESULT/COMBAT_RESULT,
        isto é 100% visual."""
        from engine.components import PlayerProjectile as _PPb, Position as _PosB
        caster_local = self._remote_players.get(payload.get("caster_eid", -1), -1)
        if caster_local == -1:
            return
        caster_pos = self.world.get_component(caster_local, _PosB)
        if not caster_pos:
            return

        if sid == "bola_de_fogo":
            tgt_local = self._resolve_local_eid(payload.get("target_eid", -1))
            if tgt_local != -1:
                self._spell_cast_system._launch_fireball(caster_local, tgt_local)
                for _, _pp, _ in self.world.get_entities_with(_PPb, _PosB):
                    if (_pp.attacker_id == caster_local and _pp.target_id == tgt_local
                            and _pp.target_server_id == -1):
                        _pp.target_server_id = -2  # cosmético: sem PROJECTILE_HIT_CS
                        break
            return

        if sid == "tiro_multiplo":
            target_eids = payload.get("target_eids", [])
        else:
            target_eids = [payload.get("target_eid", -1)]

        if sid == "flecha_reiterada":
            from content.skill_config import SKILL_CATALOG as _SC_fr
            _fr_delay  = _SC_fr.get("flecha_reiterada", {}).get("params", {}).get("arrow_delay", 0.25)
            _fr_speeds = [700.0, 640.0, 580.0]
            _n_arrows  = int(payload.get("arrow_count", 2))
            target_eids = target_eids * _n_arrows  # mesma flecha repetida N vezes no mesmo alvo
        color = {"picada_escorpiao": (60, 200, 80),
                 "tiro_multiplo":    (150, 210, 255)}.get(sid, (101, 67, 33))

        for _idx, _tgt_srv in enumerate(target_eids):
            tgt_local = self._resolve_local_eid(_tgt_srv)
            if tgt_local == -1:
                continue
            tgt_pos = self.world.get_component(tgt_local, _PosB)
            if not tgt_pos:
                continue
            _delay = (_fr_delay * _idx) if sid == "flecha_reiterada" else 0.0
            _speed = (_fr_speeds[_idx] if sid == "flecha_reiterada" and _idx < len(_fr_speeds)
                      else 700.0 if sid != "tiro_repulsivo" else 800.0)
            _eid_b = self.world.create_entity()
            self.world.add_component(_eid_b, _PosB(
                x=caster_pos.x, y=caster_pos.y, prev_x=caster_pos.x, prev_y=caster_pos.y))
            self.world.add_component(_eid_b, _PPb(
                spell_id="arrow", attacker_id=caster_local, target_id=tgt_local,
                speed=_speed, dmg_weapon_pct=1.0, dmg_sp_coeff=0.0, color=color,
                damage_type="physical", launch_delay=_delay, guaranteed_hit=True,
                target_last_x=tgt_pos.x, target_last_y=tgt_pos.y, target_server_id=-2,
            ))

    def _show_knockback_collision(self, payload: dict) -> None:
        """Tiro Repulsivo: feedback dedicado quando o empurrão colide (parede ou
        criatura) — distinto do "impact" da flecha (que já dispara sempre que o
        tiro acerta, mesmo com knockback de 0 tiles). Sem isso, uma colisão a 0
        tiles (alvo já encostado) não tinha nenhum sinal próprio — só o ícone de
        stun aparecia, dando a impressão de que o stun "aconteceu antes" do
        empurrão. Reaproveita FLT + som de impacto já existentes (arrow_impact)."""
        from ui.floating_text import FLT
        from engine.components import Position as _PosKb
        from engine.tileset import TILE_SIZE as _TS_kb

        def _local_of(server_eid: int):
            if server_eid == self._my_eid:
                return self.player_entity
            return self._remote_mobs.get(server_eid, self._remote_players.get(server_eid))

        target_eid    = payload.get("target_eid", -1)
        collided_eid  = payload.get("collided_eid", -1)
        tx, ty        = payload.get("tx", 0), payload.get("ty", 0)
        wx, wy = tx * _TS_kb + _TS_kb // 2, ty * _TS_kb + _TS_kb // 2

        _local_tgt = _local_of(target_eid)
        _tgt_pos = self.world.get_component(_local_tgt, _PosKb) if _local_tgt is not None else None
        FLT.add("Colisão!", _tgt_pos.x if _tgt_pos else wx, _tgt_pos.y if _tgt_pos else wy,
                (255, 160, 60), "small", target_id=_local_tgt if _local_tgt is not None else -1)

        if collided_eid != -1:
            _local_col = _local_of(collided_eid)
            _col_pos = self.world.get_component(_local_col, _PosKb) if _local_col is not None else None
            if _col_pos:
                FLT.add("Colisão!", _col_pos.x, _col_pos.y,
                        (255, 160, 60), "small", target_id=_local_col)

        _lx, _ly = self._player_world_pos()
        SOUNDS.play_random_at(["arrow_impact_1", "arrow_impact_2", "arrow_impact_3"],
                              wx, wy, _lx, _ly, base=0.9)

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
                from debug.aoi_debug import AOI_DBG
                AOI_DBG.log("DESPAWN_ENT", server_eid=eid, local_eid=local_eid)
                # Salva ghost position ANTES de remover entidade — fallback de FLT
                from engine.components import RemoteEntityMeta as _REM_d2
                _meta_d2 = self.world.get_component(local_eid, _REM_d2)
                if _meta_d2 and (_meta_d2.last_x or _meta_d2.last_y):
                    self._mob_ghost_pos[eid] = (_meta_d2.last_x, _meta_d2.last_y)
                # Som de morte posicional antes de remover a entidade
                try:
                    from engine.components import Position as _PosD, MobSounds as _MSD
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
            is_dash_corr   = payload.get("is_dash", False)
            player_tm = self.world.get_component(self.player_entity, TileMovement)
            if player_tm:
                # Se cliente já está dashando para o mesmo tile (prediction correta), não interrompe
                if (getattr(player_tm, "is_dash", False) and
                        player_tm.target_tile_x == real_tx and
                        player_tm.target_tile_y == real_ty):
                    pass  # animação em curso bate com posição do servidor — mantém
                elif is_dash_corr and not skill_rejected:
                    _corr_duration = payload.get("duration")
                    if _corr_duration is not None:
                        # Deslocamento forçado (ex: vítima de knockback): UM evento
                        # com posição final + duração explícita — preempta qualquer
                        # fila/dash em curso (servidor já decidiu tudo, cliente só
                        # interpola a tween confirmada, sem enfileirar passo a passo
                        # nem prever o resultado de um empurrão em si mesmo).
                        self._self_move_queue.clear()
                        from engine.utils import start_tile_movement
                        from engine.components import Position as _PosSelfDash
                        _ppos_sd = self.world.get_component(self.player_entity, _PosSelfDash)
                        if _ppos_sd:
                            start_tile_movement(_ppos_sd, player_tm, real_tx, real_ty,
                                                override_duration=_corr_duration)
                            player_tm.is_dash = _corr_duration > 0
                            self._net_last_tx = real_tx
                            self._net_last_ty = real_ty
                        return
                    # Sem duration explícito (ex: Interceptar) — comportamento
                    # existente: anima em sequência se já tiver dash em curso.
                    if player_tm.is_moving:
                        if (not self._self_move_queue
                                or self._self_move_queue[-1] != (real_tx, real_ty)):
                            self._self_move_queue.append((real_tx, real_ty))
                    else:
                        from engine.utils import start_tile_movement
                        from engine.components import Position as _PosSelfDash
                        _ppos_sd = self.world.get_component(self.player_entity, _PosSelfDash)
                        if _ppos_sd:
                            start_tile_movement(_ppos_sd, player_tm, real_tx, real_ty)
                            player_tm.is_dash       = True
                            player_tm.move_duration = 0.18
                            # _send_player_move() manda MOVE sempre que target_tile
                            # muda — sem isso, veria esta mudança (causada pelo
                            # servidor, não por input) como passo do jogador e
                            # mandaria de volta. Como o knockback já resolveu a
                            # posição final instantaneamente no servidor, esse MOVE
                            # chegaria "atrasado" pedindo um tile intermediário, seria
                            # rejeitado, e a correção de rejeição cancelaria esta
                            # própria animação — sincroniza aqui pra suprimir o envio.
                            self._net_last_tx = real_tx
                            self._net_last_ty = real_ty
                elif (player_tm.current_tile_x != real_tx or
                        player_tm.current_tile_y != real_ty or
                        skill_rejected):
                    _was_dashing = getattr(player_tm, "is_dash", False)
                    # Gap grande mesmo andando normal = desync de verdade, não
                    # correção obsoleta de 1 passo (ver comentário no else
                    # abaixo) — servidor agora só rejeita MOVE por motivo real
                    # (fora do orçamento tempo×velocidade ou atravessou parede,
                    # ver WorldServer.move_player), então uma correção com gap
                    # >1 tile aqui é sempre genuína, nunca desatualizada por
                    # delay de rede (bug real: cliente ignorava toda correção
                    # andando normal, então um desync ficava permanente pra
                    # sempre — ver ARQUITETURA_ONLINE.md).
                    from engine.utils import chebyshev as _chb_corr
                    _corr_gap = _chb_corr(player_tm.current_tile_x, player_tm.current_tile_y,
                                          real_tx, real_ty)
                    if _was_dashing or not player_tm.is_moving or _corr_gap > 1:
                        # Cancela animação de dash em andamento (predição local que o
                        # servidor não confirmou) OU, se o player está parado, é seguro
                        # aplicar a correção (nada legítimo em andamento pra corromper).
                        if _was_dashing:
                            player_tm.is_dash      = False
                            player_tm.is_moving    = False
                            player_tm.progress     = 0.0
                        player_tm.current_tile_x = real_tx
                        player_tm.current_tile_y = real_ty
                        player_tm.target_tile_x  = real_tx
                        player_tm.target_tile_y  = real_ty
                        # Sincroniza pixel position — B10
                        from engine.components import Position as _PosSync
                        _ppos = self.world.get_component(self.player_entity, _PosSync)
                        if _ppos:
                            _ppos.x = real_tx * TILE_SIZE + TILE_SIZE / 2
                            _ppos.y = real_ty * TILE_SIZE + TILE_SIZE / 2
                    # else: andando normalmente, sem dash (ex: perseguição iniciada
                    # pela própria skill que falhou — enter_combat/is_pursuing roda
                    # ANTES do handler, mesmo em caso de rejeição). Essa caminhada já
                    # está sendo validada passo-a-passo pelo pipeline normal de MOVE
                    # (server/world_server.py::move_player) — forçar esta correção
                    # (que pode estar desatualizada pelo delay de rede desde quando
                    # foi gerada) sobrescreveria um movimento legítimo mais recente
                    # com current==target e is_moving ainda True, deixando o
                    # TileMovementSystem reanimar a partir de pixels obsoletos no
                    # próximo frame. Ignora a correção de posição; mantém só o feedback.
                    if skill_rejected:
                        # Feedback imediato: avisa que o dash foi bloqueado
                        from ui.floating_text import FLT
                        from engine.components import Position as _PosRej
                        _pos_rej = self.world.get_component(self.player_entity, _PosRej)
                        if _pos_rej:
                            FLT.add("Bloqueado!", _pos_rej.x, _pos_rej.y,
                                    (255, 80, 80), "small",
                                    target_id=self.player_entity)
        elif eid in self._remote_players:
            self._apply_remote_move(eid, payload.get("tx", 0), payload.get("ty", 0))

    def _handle_msg_aoi_update(self, payload: dict) -> None:
        # spawned ANTES de moved: entidades que entram no AOI e já se movem no
        # MESMO tick (ex: mob voltando a perseguir, knockback empurrando algo
        # de volta ao range) precisam existir no ECS local antes de receber o
        # passo de movimento — senão _move_remote_mob/_apply_remote_move
        # descartam o move (local_eid ainda None) e a entidade só "pisca" na
        # posição final via spawn, sem nenhuma animação. Padrão genérico: vale
        # para QUALQUER skill/sistema que mova entidades via _moved_this_tick.
        for sp in payload.get("spawned", []):
            eid  = sp.get("eid", -1)
            kind = sp.get("kind", "player")
            if eid != -1 and eid != self._my_eid:
                if kind in ("enemy", "mob_projectile"):
                    from debug.aoi_debug import AOI_DBG
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
                    "kind":     kind,
                })
        for m in payload.get("moved", []):
            eid = m.get("eid", -1)
            if eid == self._my_eid:
                continue
            if eid in self._remote_players:
                self._apply_remote_move(eid, m["tx"], m["ty"],
                                        from_tx=m.get("from_tx"),
                                        from_ty=m.get("from_ty"),
                                        is_dash=m.get("is_dash", False),
                                        teleport=m.get("teleport", False),
                                        duration=m.get("duration"))
            elif eid in self._remote_mobs:
                self._move_remote_mob(eid, m["tx"], m["ty"],
                                      m.get("from_tx"), m.get("from_ty"),
                                      is_dash=m.get("is_dash", False),
                                      duration=m.get("duration"))
        # Status effects sync (antes de combat para ter CC certo na animação)
        for eff_payload in payload.get("effects", []):
            if eff_payload.get("eid") == self._my_eid:
                self._sync_player_effects(eff_payload["effects"])
        # Status effects em mobs remotos (ícones acima da barra + LOG de CC).
        # Chamado SEMPRE (mesmo com dict vazio) — o early-clear dentro de
        # _sync_mob_effects depende de rodar a cada tick para remover ícones
        # de efeitos que o servidor removeu (ex: sono cancelado a meio do canal),
        # senão o ícone local persiste contando sozinho via StatusEffectSystem do cliente.
        _mob_efx = payload.get("mob_effects", {})
        self._sync_mob_effects(_mob_efx)
        # Combat ANTES de despawned: garante floating text do golpe fatal
        # antes do mob ser removido de _remote_mobs
        for cr in payload.get("combat", []):
            self._apply_combat_result(cr)
        _died_eids = set(payload.get("died_eids", []))
        for eid in payload.get("despawned", []):
            self._remove_remote_player_entity(eid)
            self._remote_players.pop(eid, None)
            local_eid = self._remote_mobs.pop(eid, None)
            _pending_queue = self._mob_move_queues.get(eid)
            if local_eid is not None:
                from debug.aoi_debug import AOI_DBG
                AOI_DBG.log("DESPAWN_AOI", server_eid=eid, local_eid=local_eid)
                # Salva ghost position ANTES de remover entidade — fallback de FLT
                from engine.components import RemoteEntityMeta as _REM_d
                _meta_d = self.world.get_component(local_eid, _REM_d)
                if _meta_d and (_meta_d.last_x or _meta_d.last_y):
                    self._mob_ghost_pos[eid] = (_meta_d.last_x, _meta_d.last_y)
                _is_kill = eid in _died_eids
                # Som de morte APENAS para kills reais (não para saída de AOI).
                if _is_kill:
                    try:
                        from engine.components import Position as _PosD2, MobSounds as _MSD2
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
                from engine.components import TileMovement as _TM_dep, Position as _Pos_dep
                from engine.tileset import TILE_SIZE as _TS_dep
                _tm_dep  = self.world.get_component(local_eid, _TM_dep)
                _pos_dep = self.world.get_component(local_eid, _Pos_dep)
                _defer = False
                if _is_kill and _pending_queue:
                    # Sequência de movimento em cadeia pendente (knockback/dash multi-tile):
                    # deixa _process_mob_move_queues drenar tudo normalmente antes de
                    # remover a entidade — padrão genérico para qualquer "fast move"
                    # (is_dash) que termine em morte no meio da animação. Esses passos
                    # escrevem a posição final no servidor sem interpolação própria
                    # (igual à escrita instantânea de tile), então o loot já nasce no
                    # tile certo — sem precisar de redirect aqui.
                    self._pending_mob_despawn[local_eid] = {"server_eid": eid, "timer": 0.0}
                    _defer = True
                elif _is_kill and _tm_dep is not None and _tm_dep.is_moving:
                    if _tm_dep.progress >= 0.3:
                        # Mob commitou para o próximo tile: termina animação lá e loot segue.
                        _srv_tx = _tm_dep.current_tile_x
                        _srv_ty = _tm_dep.current_tile_y
                        _tgt_tx = _tm_dep.target_tile_x
                        _tgt_ty = _tm_dep.target_tile_y
                        self._pending_mob_despawn[local_eid] = {"server_eid": eid, "timer": 0.0}
                        self._pending_loot_redirect[(_srv_tx, _srv_ty)] = (_tgt_tx, _tgt_ty)
                        _defer = True
                    else:
                        # Menos de 30% do passo: snap curto de volta ao tile do servidor.
                        _snap_tx = _tm_dep.current_tile_x
                        _snap_ty = _tm_dep.current_tile_y
                        _tm_dep.is_moving     = False
                        _tm_dep.target_tile_x = _snap_tx
                        _tm_dep.target_tile_y = _snap_ty
                        _tm_dep.progress      = 0.0
                        if _pos_dep:
                            _pos_dep.x = _snap_tx * _TS_dep + _TS_dep // 2
                            _pos_dep.y = _snap_ty * _TS_dep + _TS_dep // 2
                if not _defer:
                    self._mob_move_queues.pop(eid, None)
                    try:
                        self.world.remove_entity(local_eid)
                    except Exception:
                        pass

    def _handle_msg_skill_levels_update(self, payload: dict) -> None:
        """SKILL_LEVELS_UPDATE: snapshot completo de levels/xp do skill level
        (Tibia-like), enviado só pro dono pelo servidor sempre que muda (cast
        de magia, auto-attack, arco, DoT resistido — ver
        WorldServer._sync_player_skill_levels_dirty). Substitui (não soma) o
        componente local — só pra exibição no painel (tecla L), nunca usado
        em cálculo de dano client-side."""
        from engine.components import SkillLevels
        skl = self.world.get_component(self.player_entity, SkillLevels)
        if not skl:
            return
        for sid, lvl in (payload.get("levels") or {}).items():
            if sid in skl.levels:
                skl.levels[sid] = int(lvl)
        for sid, xp in (payload.get("xp") or {}).items():
            if sid in skl.xp:
                skl.xp[sid] = int(xp)

        # Feedback de level-up de skill: texto no log de combate + centro da
        # tela (igual Tibia) + som (mesmo som de level-up do personagem, por
        # enquanto — ver stats_system.py).
        from ui.skill_level_ui import SKILL_LABELS
        for entry in (payload.get("leveled_up") or []):
            _label = SKILL_LABELS.get(entry.get("skill_id", ""), entry.get("skill_id", ""))
            _msg = (f"Parabéns, você subiu o nível de sua habilidade com "
                    f"{_label} para o nível {entry.get('level')}.")
            LOG.add(_msg, (255, 200, 0))
            PROC.add(_msg, (255, 200, 0), duration=5.0)
            SOUNDS.play_ui("levelup")

    def _handle_msg_quest_update(self, payload: dict) -> None:
        """QUEST_UPDATE: snapshot completo de active/completed do QuestLog,
        enviado só pro dono pelo servidor sempre que muda (evento de
        progresso, QUEST_ACCEPT, QUEST_TURN_IN — ver
        WorldServer._process_quest_events / server/session.py). Substitui
        (não soma) o componente local — servidor é o único produtor
        autoritativo de progresso/entrega no modo online (ver
        quest_logic.py/PROBLEMAS_ARQUITETURA.md)."""
        from engine.components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return
        ql.active = {q: list(p) for q, p in (payload.get("active") or {}).items()}
        ql.completed = set(payload.get("completed") or [])

        completed_qid = payload.get("completed_qid", "")
        if completed_qid:
            from content.quests_data import QUESTS
            qdef = QUESTS.get(completed_qid)
            if qdef:
                parts = []
                if qdef.reward.xp:   parts.append(f"+{qdef.reward.xp} XP")
                if qdef.reward.gold: parts.append(f"+{qdef.reward.gold} ouro")
                reward_str = f" ({', '.join(parts)})" if parts else ""
                LOG.add(f'Quest completa: "{qdef.title}"{reward_str}!', (255, 215, 0))
                PROC.add("Quest Completa!", (255, 215, 0))

    def _handle_msg_stats_update(self, payload: dict) -> None:
        from engine.components import CombatStats, RemoteControlled
        eid = payload.get("eid", -1)
        # Projétil cosmético de espectador: ver _handle_msg_skill_effect (event="launch").
        # Antes vivia aqui, num canal STATS_UPDATE per-player sem filtro de AOI e com uma
        # allowlist de campos em session.py que cada skill nova esquecia de estender —
        # causa raiz do bug "flecha não aparece pro remoto" (Tiro Múltiplo). Consolidado
        # no SKILL_EFFECT, que já é broadcast AOI automático pra qualquer skill. Ver
        # arquitetura/PROBLEMAS_ARQUITETURA.md.

        if eid == self._my_eid:
            # Confirmação/rejeição de CONSUMABLE_USE — SÓ AGORA o item sai da
            # bag local (ver ConsumableSystem._use_consumable/_finalize_consumable).
            # Sem esperar isso, um consumível bloqueado no servidor (drift de
            # HP/mana entre cliente e servidor) perdia o item em silêncio, sem
            # curar de verdade e sem nenhum aviso — ver PROBLEMAS_ARQUITETURA.md.
            _cons_item = payload.get("item_name")
            if _cons_item:
                if payload.get("consumable_ok"):
                    self._consumable_system._finalize_consumable(self.player_entity, _cons_item)
                elif payload.get("consumable_rejected"):
                    from ui.floating_text import WARN as _WARN_cons
                    _cons_msgs = {
                        "hp_full":   "HP já está cheio",
                        "mana_full": "Mana já está cheia",
                        "in_combat": "Não pode usar em combate",
                    }
                    _WARN_cons.add(_cons_msgs.get(payload.get("reason", ""),
                                                  "Não foi possível usar o item"))
                if self._consumable_system.pending_item_name == _cons_item:
                    self._consumable_system.pending_item_name = ""

            # Confirmação de Recarregar — o servidor já reabasteceu a aljava
            # de verdade; aqui só espelhamos o resultado real na bag/aljava
            # LOCAIS. Sem isso, o cliente nunca ficava sabendo do reload
            # (arrow_count/munição da bag divergiam do servidor pra sempre
            # após o primeiro uso online — bug real "aljava diz estar cheia
            # mas não está"). Ver PROBLEMAS_ARQUITETURA.md.
            if "quiver_arrow_count" in payload:
                from engine.components import Equipment as _EqRec
                _eq_rec = self.world.get_component(self.player_entity, _EqRec)
                _quiver_rec = _eq_rec.slots.get("offhand") if _eq_rec else None
                if _quiver_rec is not None:
                    _quiver_rec.arrow_count = payload["quiver_arrow_count"]
                    # max_arrows: servidor é quem resolve o fallback de aljavas
                    # legadas (max_arrows==0 -> 100) — sem espelhar aqui, a cópia
                    # LOCAL ficava travada em 0 pra sempre (HUD mostrava "100/0").
                    # Dinâmico por item: aljavas de tier maior (125/150...) mandam
                    # o próprio valor, não hardcoded aqui.
                    _max_rec = payload.get("quiver_max_arrows")
                    if _max_rec:
                        _quiver_rec.max_arrows = _max_rec
                    _subtype_rec = payload.get("quiver_subtype")
                    if _subtype_rec:
                        _quiver_rec.subtype = _subtype_rec
                _ammo_name_rec  = payload.get("ammo_name", "")
                _ammo_taken_rec = payload.get("ammo_taken", 0)
                if _ammo_name_rec and _ammo_taken_rec > 0:
                    from engine.components import Inventory as _InvRec
                    _inv_rec = self.world.get_component(self.player_entity, _InvRec)
                    if _inv_rec:
                        _ammo_item_rec = next(
                            (it for it in _inv_rec.items
                             if it is not None and it.name == _ammo_name_rec), None)
                        if _ammo_item_rec is not None:
                            _ammo_item_rec.stack -= _ammo_taken_rec
                            if _ammo_item_rec.stack <= 0:
                                _inv_rec.items.remove(_ammo_item_rec)

            # Gold — hoje só usado pela recompensa de quest (_handle_quest_turn_in
            # já persiste certo, mas sem isso o cliente só via o valor novo no
            # próximo relogin — bug real reportado por testers). Valor
            # ABSOLUTO (não delta), mesmo padrão de hp/hp_max abaixo.
            if "gold" in payload:
                from engine.components import Wallet as _WalletGold
                _wallet_gold = self.world.get_component(self.player_entity, _WalletGold)
                if _wallet_gold:
                    _wallet_gold.gold = payload["gold"]

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
                from engine.components import CharacterStats as _CSST
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
                from engine.components import Position as _PosMR
                _pos_mr = self.world.get_component(self.player_entity, _PosMR)
                if _pos_mr:
                    from ui.floating_text import FLT as _FLT_mr
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
                from ui.floating_text import FLT as _FLT_heal
                from engine.components import Position as _PosHeal
                _pos_h = self.world.get_component(self.player_entity, _PosHeal)
                if _pos_h:
                    _FLT_heal.add(f"+{_heal_amt} HP", _pos_h.x, _pos_h.y - 20,
                                  (100, 255, 120), size="normal",
                                  target_id=self.player_entity)
            # XP ganho (notificação do servidor — XP proporcional por dano)
            xp_gained = payload.get("xp_gained", 0)
            if xp_gained > 0:
                from engine.components import CharacterStats, Position, PermanentStats
                from engine.stats_system import process_levelups
                char_stats = self.world.get_component(self.player_entity, CharacterStats)
                cs_xp      = self.world.get_component(self.player_entity, CombatStats)
                perm_xp    = self.world.get_component(self.player_entity, PermanentStats)
                if char_stats:
                    char_stats.current_xp += xp_gained
                    # Servidor é autoritativo para pontos de talento — não dá localmente
                    process_levelups(self.world, self.player_entity,
                                     char_stats, cs_xp, perm_xp,
                                     give_talent_points=False)
                from ui.floating_text import FLT
                pos = self.world.get_component(self.player_entity, Position)
                if pos:
                    FLT.add(f"+{xp_gained} XP", pos.x, pos.y - 20, (100, 255, 100), size="small",
                            target_id=self.player_entity)
            # Level-up: HP e pontos de talento autoritativos do servidor
            _srv_tp = payload.get("talent_points")
            if _srv_tp is not None:
                from engine.components import TalentTree as _TTsync
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
                            from ui.floating_text import WARN
                            WARN.add("Vitória Iminente!")
                            break
        # PvP: aplica efeitos recebidos pelo próprio jogador (vítima)
        if eid == self._my_eid and payload.get("applied_effects"):
            from engine.core_systems import apply_effect as _ae_pvp
            _ae_pvp_durs = payload.get("effect_durations", {})
            for _ae_pvp_ef in payload["applied_effects"]:
                _ae_pvp_dur = _ae_pvp_durs.get(_ae_pvp_ef, 5.0)
                _ae_pvp(self.world, self.player_entity, _ae_pvp_ef, _ae_pvp_dur)
                # Para movimento imediatamente ao receber CC via PvP
                if _ae_pvp_ef in ("root", "stun", "polymorph", "disoriented"):
                    from engine.components import CombatState as _CStAE, TileMovement as _TMAE
                    from engine.components import PlayerAutoMove as _PAMAE
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
        # Servidor declarou que o player local morreu. Corpo fica no local da
        # morte — sem restauração de HP/mana nem teleporte aqui. O cliente
        # inicia o timer de 2s pra mostrar a janela "Você morreu".
        from engine.components import CombatStats, CombatState, GhostState
        _cs_death = self.world.get_component(self.player_entity, CombatStats)
        if _cs_death:
            _cs_death.current_hp = 0  # garante HP=0 mesmo sem COMBAT_RESULT do golpe fatal
        gst = self.world.get_component(self.player_entity, GhostState)
        if gst:
            gst.is_dead   = True
            gst.is_ghost  = False
            gst.corpse_tx = payload.get("corpse_tx", -1)
            gst.corpse_ty = payload.get("corpse_ty", -1)
            gst.graveyard_timer = 0.0
            gst.near_corpse = False
        combat_state = self.world.get_component(self.player_entity, CombatState)
        if combat_state:
            combat_state.target_entity_id = -1
            combat_state.is_pursuing      = False
            combat_state.is_alive         = False  # can_act()==False: bloqueia skills/ataques
        self._net_last_target = -1
        self._death_timer = 0.0

    def _handle_msg_player_revive(self, payload: dict) -> None:
        # Servidor reviveu o player local (cemitério ou corpo) — restaura
        # hp/mana, teleporta pro destino e limpa o GhostState.
        from engine.components import CombatStats, CombatState, CharacterStats as _CHS_r, GhostState, Position as _PosR
        cs     = self.world.get_component(self.player_entity, CombatStats)
        char_r = self.world.get_component(self.player_entity, _CHS_r)
        if cs:
            cs.max_hp     = payload.get("hp_max", cs.max_hp)
            cs.current_hp = payload.get("hp", cs.current_hp)
            cs.mana       = payload.get("mana", cs.mana)  # espelho — ver CombatStats.mana
        if char_r:
            char_r.max_mana = payload.get("max_mana", char_r.max_mana)
            char_r.mana     = payload.get("mana", char_r.mana)

        tx = payload.get("tx", 0)
        ty = payload.get("ty", 0)
        ptm = self.world.get_component(self.player_entity, TileMovement)
        ppo = self.world.get_component(self.player_entity, _PosR)
        if ptm:
            ptm.current_tile_x = ptm.target_tile_x = tx
            ptm.current_tile_y = ptm.target_tile_y = ty
            ptm.is_moving = False
            ptm.progress  = 0.0
        if ppo:
            ppo.x = tx * TILE_SIZE + TILE_SIZE // 2
            ppo.y = ty * TILE_SIZE + TILE_SIZE // 2

        gst = self.world.get_component(self.player_entity, GhostState)
        if gst:
            gst.is_dead   = False
            gst.is_ghost  = False
            gst.corpse_tx = -1
            gst.corpse_ty = -1
            gst.graveyard_timer = 0.0
            gst.near_corpse = False

        combat_state = self.world.get_component(self.player_entity, CombatState)
        if combat_state:
            combat_state.target_entity_id = -1
            combat_state.is_pursuing      = False
            combat_state.is_alive         = True
        self._net_last_target = -1
        self._death_timer = 0.0

    def _handle_msg_ghost_state(self, payload: dict) -> None:
        # Atualiza estado do espírito local (raio do corpo / timer do cemitério).
        from engine.components import GhostState, Position as _PosGS
        gst = self.world.get_component(self.player_entity, GhostState)
        if not gst:
            return
        gst.is_ghost        = payload.get("is_ghost", gst.is_ghost)
        gst.near_corpse     = payload.get("near_corpse", gst.near_corpse)
        gst.graveyard_timer = payload.get("graveyard_timer", gst.graveyard_timer)
        # Resincroniza o contador LOCAL de exibição (death_ui_handlers.py::
        # _ghost_timer) com o valor autoritativo do servidor — sem isso, ele
        # incrementa sem parar independente do player estar dentro do raio
        # do cemitério, e mostrava "revive em Xs" contando até 0 mesmo com o
        # timer real do servidor zerado (saiu do raio) — nunca revivia.
        if "graveyard_timer" in payload:
            self._ghost_timer = payload["graveyard_timer"]

        # Liberação do espírito: servidor manda a posição do cemitério —
        # teleporta já no mesmo tick (sem isso, a posição local só é
        # corrigida na próxima vez que o player tentar se mover).
        if "tx" in payload and "ty" in payload:
            tx, ty = payload["tx"], payload["ty"]
            ptm = self.world.get_component(self.player_entity, TileMovement)
            ppo = self.world.get_component(self.player_entity, _PosGS)
            if ptm:
                ptm.current_tile_x = ptm.target_tile_x = tx
                ptm.current_tile_y = ptm.target_tile_y = ty
                ptm.is_moving = False
                ptm.progress  = 0.0
            if ppo:
                ppo.x = tx * TILE_SIZE + TILE_SIZE // 2
                ppo.y = ty * TILE_SIZE + TILE_SIZE // 2

    def _handle_msg_entity_death(self, payload: dict) -> None:
        # Broadcast pra AOI: outro player morreu no tile (tx, ty). Tinge o
        # sprite remoto em tom acinzentado (corpo) já neste momento — o
        # marcador "player_corpse" só aparece depois, quando o espírito é
        # liberado e a entidade original some do AOI.
        eid = payload.get("eid", -1)
        local_eid = self._remote_players.get(eid)
        if local_eid is None:
            return
        from engine.components import RemoteControlled, Renderable
        rc = self.world.get_component(local_eid, RemoteControlled)
        if rc:
            rc.hp = 0
        rnd = self.world.get_component(local_eid, Renderable)
        if rnd:
            rnd.color = tuple(int(c * 0.35) + 20 for c in rnd.color[:3])

    def _handle_msg_loot_available(self, payload: dict) -> None:
        # Servidor concedeu loot ao player local.
        # Cria entidade Corpse no ECS local para o LootSystem offline
        # funcionar IDENTICAMENTE ao offline (modal, equip, coins, scroll).
        from engine.entity_factory import create_corpse
        from content.loot_tables import _T
        from engine.tileset import TILE_SIZE as _TS
        corpse_id = payload.get("corpse_id", -1)
        coins     = payload.get("coins", 0)
        tx        = payload.get("tx", 0)
        ty        = payload.get("ty", 0)
        if corpse_id < 0:
            return
        # Se mob morreu commitado para o próximo tile, loot segue para lá.
        _redirect = self._pending_loot_redirect.pop((tx, ty), None)
        if _redirect:
            tx, ty = _redirect
            px = tx * _TS + _TS // 2
            py = ty * _TS + _TS // 2
        # Reconstrói objetos de item a partir dos dados serializados do servidor
        from content.quests_data import QUEST_ITEMS as _QI_loot
        loot_items = []
        for item_data in payload.get("items", []):
            item_name = item_data.get("name", "")
            _matched = False
            for _key, factory in _T.items():
                try:
                    candidate = factory()
                except Exception:
                    continue
                if getattr(candidate, "name", "") == item_name:
                    candidate.stack = item_data.get("stack", 1)
                    loot_items.append(candidate)
                    _matched = True
                    # Reciclagem: única fonte de loot de ammo com stack>1 hoje —
                    # mesmo aviso do offline (systems.py), aqui no momento do drop.
                    if candidate.item_type == "ammo" and candidate.stack > 1:
                        LOG.add(f"Reciclagem! {candidate.stack} flechas no loot.",
                                (180, 220, 120))
                    break
            # Itens de quest (drop condicional, ex: Presa de Lobo) vivem em
            # QUEST_ITEMS, não em loot_tables._T — sem este fallback o item
            # dropava no servidor mas era DESCARTADO aqui na reconstrução e
            # nunca aparecia na janela de loot (bug real: "Presas Afiadas").
            if not _matched:
                _qi_factory = _QI_loot.get(item_name)
                if _qi_factory:
                    try:
                        candidate = _qi_factory()
                        candidate.stack = item_data.get("stack", 1)
                        loot_items.append(candidate)
                    except Exception:
                        pass
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
        from engine.tileset import TILE_SIZE as _TS_snd
        _ev_sx = _ev_tx * _TS_snd + _TS_snd // 2
        _ev_sy = _ev_ty * _TS_snd + _TS_snd // 2
        _elx, _ely = self._player_world_pos()
        if _ev_kind == "mob_aggro":
            # Usa MobSounds component se o mob estiver no AOI do cliente
            _ev_local = self._remote_mobs.get(_ev_seid)
            _ev_snd   = None
            if _ev_local is not None:
                from engine.components import MobSounds as _MSev
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
            from engine.components import Inventory as _InvBR, Wallet as _WalBR
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
            from ui.combat_log import LOG as _LOG_BR
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
            from ui.floating_text import WARN as _WARN_BR
            _WARN_BR.add(_reason_msg)

    def _handle_msg_sell_result(self, payload: dict) -> None:
        from engine.components import Wallet as _WalSR
        wal_sr = self.world.get_component(self.player_entity, _WalSR)
        if payload.get("success"):
            # Servidor confirma — atualiza gold autoritativo
            if wal_sr is not None:
                wal_sr.gold = payload.get("new_gold", wal_sr.gold)
            from ui.combat_log import LOG as _LOG_SR
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
            from ui.floating_text import WARN as _WARN_SR
            _WARN_SR.add(payload.get("reason", "Venda recusada"))

    def _handle_msg_equip_rejected(self, payload: dict) -> None:
        """Servidor recusou um slot do último EQUIP_SYNC (classe ou level
        insuficiente — ver server/world_server.py::update_player_equipment,
        único ponto que valida CLASS_ARMOR_ALLOWED/is_weapon_allowed_for_class/
        level_requirement de verdade, já que o check do cliente em
        _equip_item é só UX otimista).
        Reverte o slot local: tira o item que foi otimisticamente equipado e
        devolve pra bag (nunca perde o item), e resincroniza."""
        from engine.components import Equipment as _EqRej, Inventory as _InvRej
        slot      = payload.get("slot", "")
        reason    = payload.get("reason", "")
        item_name = payload.get("item_name", "item")
        equip = self.world.get_component(self.player_entity, _EqRej)
        inv   = self.world.get_component(self.player_entity, _InvRej)
        if equip is not None and slot in equip.slots:
            rejected_item = equip.slots.get(slot)
            equip.slots[slot] = None
            if (rejected_item is not None and inv is not None
                    and len(inv.items) < inv.max_slots):
                inv.items.append(rejected_item)
        _reason_msg = {
            "class": "sua classe não pode usar esse item",
            "level": "level insuficiente",
        }.get(reason, "requisito não atendido")
        from ui.floating_text import WARN as _WARN_EqR
        _WARN_EqR.add(f"Não foi possível equipar {item_name}: {_reason_msg}")
        self._send_equip_sync()

    def _handle_msg_pong(self, payload: dict) -> None:
        if self._net:
            rtt = int(__import__("time").time() * 1000) - payload.get("client_ts", 0)
            self._net.latency_ms = rtt

    def _handle_msg_zone_change(self, payload: dict) -> None:
        """Servidor autorizou troca de mapa. Executa _do_transition no cliente."""
        map_file = payload.get("map_file", "")
        target_x = int(payload.get("target_x", 0))
        target_y = int(payload.get("target_y", 0))
        if map_file:
            self._do_transition({
                "target_map": map_file,
                "target_x":   target_x,
                "target_y":   target_y,
            })

    # ── Trade (player↔player) — ver server/trade_processor.py ────────────────

    def _handle_msg_trade_invite(self, payload: dict) -> None:
        tui = self._get_trade_ui()
        if tui is None:
            return
        tui.pending_invite_from_eid  = payload.get("from_eid", -1)
        tui.pending_invite_from_name = payload.get("from_name", "?")
        from ui.sound_manager import SOUNDS as _SND_TI
        _SND_TI.play_ui("levelup")

    def _handle_msg_trade_open(self, payload: dict) -> None:
        tui = self._get_trade_ui()
        if tui is None:
            return
        tui.reset()
        tui.clear_invite()
        tui.trade_id   = payload.get("trade_id", -1)
        tui.other_eid  = payload.get("other_eid", -1)
        tui.other_name = payload.get("other_name", "?")
        tui.popup_target_eid = -1

    def _handle_msg_trade_state(self, payload: dict) -> None:
        """Único ponto onde um item ofertado sai da Inventory local de
        verdade (nunca otimista — ver TradeUIState/plano do trade). Compara
        (via multiset por nome) a oferta antiga com a nova pra decidir o que
        remover/devolver — mesmo racional do match-por-nome já usado em
        Recarregar."""
        tui = self._get_trade_ui()
        if tui is None or tui.trade_id != payload.get("trade_id", -1):
            return
        from collections import Counter
        from engine.components import Inventory as _InvTS, Wallet as _WalTS
        inv = self.world.get_component(self.player_entity, _InvTS)
        wal = self.world.get_component(self.player_entity, _WalTS)

        old_items = list(tui.my_offer)
        new_data  = payload.get("my_offer", [])
        old_counter = Counter(getattr(it, "name", "") for it in old_items)
        new_counter = Counter(d.get("name", "") for d in new_data)
        newly_offered = new_counter - old_counter   # sai da bag
        newly_withdrawn = old_counter - new_counter  # volta pra bag

        if inv is not None:
            for name, cnt in newly_offered.items():
                for _ in range(cnt):
                    for idx, it in enumerate(inv.items):
                        if it.name == name:
                            inv.items.pop(idx)
                            break
            for name, cnt in newly_withdrawn.items():
                matched = 0
                for it in old_items:
                    if matched >= cnt:
                        break
                    if getattr(it, "name", "") == name:
                        inv.items.append(it)
                        matched += 1

        new_my_gold = payload.get("my_gold", 0)
        if wal is not None:
            wal.gold -= (new_my_gold - tui.my_gold)

        tui.my_offer        = [self._item_from_data(d) for d in new_data]
        tui.their_offer     = [self._item_from_data(d) for d in payload.get("their_offer", [])]
        tui.my_gold          = new_my_gold
        tui.their_gold       = payload.get("their_gold", 0)
        tui.my_confirmed     = payload.get("my_confirmed", False)
        tui.their_confirmed  = payload.get("their_confirmed", False)

    def _handle_msg_trade_result(self, payload: dict) -> None:
        tui = self._get_trade_ui()
        from engine.components import Inventory as _InvTR, Wallet as _WalTR
        inv = self.world.get_component(self.player_entity, _InvTR)
        wal = self.world.get_component(self.player_entity, _WalTR)
        for d in payload.get("received_items", []):
            it = self._item_from_data(d)
            if it and inv is not None and len(inv.items) < inv.max_slots:
                inv.items.append(it)
        if wal is not None:
            wal.gold += payload.get("received_gold", 0)
        from ui.combat_log import LOG as _LOG_TR
        _LOG_TR.add("Troca concluída!", (120, 220, 120))
        if tui is not None:
            tui.reset()
        self._send_save_state()

    def _handle_msg_trade_cancelled(self, payload: dict) -> None:
        tui = self._get_trade_ui()
        if tui is None:
            return
        if tui.trade_id != -1:
            # Devolve pra Inventory/Wallet local o que estava em my_offer/my_gold
            # (o servidor já devolveu de verdade — isso só sincroniza a cópia local).
            from engine.components import Inventory as _InvTC, Wallet as _WalTC
            inv = self.world.get_component(self.player_entity, _InvTC)
            wal = self.world.get_component(self.player_entity, _WalTC)
            if inv is not None:
                for it in tui.my_offer:
                    if len(inv.items) < inv.max_slots:
                        inv.items.append(it)
            if wal is not None:
                wal.gold += tui.my_gold
        tui.reset()
        tui.clear_invite()
        reason = payload.get("reason", "")
        _reason_msg = {
            "declined":        "Convite de troca recusado",
            "cancelled":       "Troca cancelada",
            "distance":        "Troca cancelada: jogador saiu de alcance",
            "disconnect":      "Troca cancelada: jogador desconectou",
            "inventory_full":  "Troca cancelada: mochila cheia",
            "invalid":         "Troca inválida",
        }.get(reason, "Troca cancelada")
        from ui.floating_text import WARN as _WARN_TC
        _WARN_TC.add(_reason_msg)
        self._send_save_state()

    def _handle_msg_chat_message(self, payload: dict) -> None:
        """Mensagem de chat confirmada pelo servidor (server/session.py::
        _handle_chat) — inclui o próprio remetente, já que o AOI (canal
        "local") ou o broadcast global (canal "world") sempre engloba o
        dono. Cliente nunca ecoa a mensagem antes desta chegada. Roteada
        pro histórico da aba certa (client/chat_handlers.py) por `channel`."""
        sender  = payload.get("sender", "?")
        text    = payload.get("text", "")
        color   = tuple(payload.get("color", [220, 210, 150]))
        channel = payload.get("channel", "local")
        target  = self._chat_world if channel == "world" else self._chat_local
        target.append((sender, text, color))

        entity_id = self._resolve_chat_sender_entity(sender)
        if entity_id != -1:
            from ui.chat_bubble import CHAT_BUBBLE
            CHAT_BUBBLE.add(entity_id, text)

    def _resolve_chat_sender_entity(self, sender_name: str) -> int:
        """Resolve o nome do remetente pro eid LOCAL (próprio player ou
        player remoto) — usado só pra posicionar o balão de fala; se não
        achar (mob/NPC não manda chat, ou remetente já desconectou), -1.

        `sender` em CHAT_MESSAGE é `Session.display_name` (nome do
        PERSONAGEM — ver server/session.py::Session.display_name/
        _handle_chat). Bug real corrigido nesta rodada: o servidor mandava
        `session.username` (login da conta) em vários lugares — chat,
        nameplate (ENTITY_SPAWN/AOI_UPDATE de player) e trade — só
        coincidia com o nome do personagem quando o jogador escolhia os
        dois iguais. Agora tudo usa `display_name` de forma consistente,
        então comparar com `_logged_char_name` (nome do PRÓPRIO
        personagem, setado no login) é a checagem certa de novo, e
        `RemoteControlled.name` (branch de player remoto abaixo) também
        já vem como nome do personagem do outro lado."""
        if sender_name == getattr(self, "_logged_char_name", None):
            return self.player_entity
        from engine.components import RemoteControlled as _RCchat
        for eid, rc in self.world.get_entities_with(_RCchat):
            if rc.name == sender_name:
                return eid
        return -1
