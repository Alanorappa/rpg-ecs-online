"""
network_handlers.py — Mixin com os handlers de mensagens de rede do cliente.
Separado de game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.world, self._my_eid, self._net e os demais atributos referenciados aqui.
"""
import random
from engine.components import PlayerSkills, TileMovement
from engine.tileset import TILE_SIZE
from shared.messages import MsgType
from ui.combat_log import LOG
from ui.floating_text import PROC
from ui.sound_manager import SOUNDS


class NetworkHandlers:

    def _handle_net_message(self, msg_type, payload: dict) -> None:
        """Dispatch O(1) via dict de módulo (`_NET_DISPATCH_NAMES`, montado
        logo após a definição desta classe, no fim do arquivo) — antes
        disso era uma cadeia if/elif de 54 ramos percorrida sequencialmente
        pra CADA mensagem recebida do servidor (AOI_UPDATE/ENTITY_MOVE/
        COMBAT_RESULT chegam em rajada durante combate, servidor tickando a
        30Hz — ver arquitetura/CLIENT_PERFORMANCE_REVIEW.md item 7).

        Guarda NOME (string) + `getattr(self, name)` em vez de referência
        direta de função — 17 dos 54 handlers (duelo/grupo/arena/BG) não
        são definidos nesta classe, e sim em mixins IRMÃOS
        (`DuelHandlers`/`PartyHandlers`/`ArenaHandlers`/
        `BattlegroundHandlers`/`BgQueueHandlers`, combinados em
        `GameEngine`, ver game.py) — só existem no MRO de `self` em
        runtime, nunca em `NetworkHandlers` isolada. Referenciar a função
        direto pela classe (`NetworkHandlers._handle_msg_x`) quebraria
        esses 17 com AttributeError no import do módulo (achado corrigindo
        este item — a 1ª versão fazia exatamente isso)."""
        name = self._NET_DISPATCH_NAMES.get(msg_type)
        if name:
            getattr(self, name)(payload)

    def _handle_msg_char_stats_data(self, payload: dict) -> None:
        self._char_stats_ui.set_data(payload)


    def _handle_msg_login_ok(self, payload: dict) -> None:
        from debug.hotbar_slot_debug import HSD, DBG_ENABLED as _HSD_ON_lok
        if _HSD_ON_lok:
            HSD.log("LOGIN_OK_RECEIVED", eid=payload.get("eid"),
                    map_id=payload.get("char", {}).get("map_id"))
        from engine.components import TileMovement
        # Reseta espelhamento de players remotos de uma sessão anterior no
        # MESMO processo de cliente (reconectar sem fechar o jogo). Sem isso,
        # se o servidor reiniciar e reciclar os mesmos server_eids, o guard
        # de _spawn_remote_player_entity ("if server_eid in self._remote_players:
        # return") acha que a entidade já existe e NUNCA aplica o payload
        # novo (level/hp/nome atuais) — a entidade ECS antiga (com level
        # desatualizado) sobrevive até um evento que sobrescreva por
        # inteiro (ex: STATS_UPDATE de HP), o que parecia "level só
        # atualiza quando o HP muda" (bug relatado 18/07/2026).
        for _local_eid in self._remote_players.values():
            try:
                self.world.remove_entity(_local_eid)
            except Exception:
                pass
        self._remote_players.clear()
        self._remote_player_move_queues.clear()
        self._remote_step_timers.clear()
        self._my_eid = payload.get("eid", -1)
        self.is_gm   = bool(payload.get("is_gm", False))
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
        # Guarda o id do personagem (03/10/2026) — usado por
        # _handle_msg_auth_ok pra re-selecionar automaticamente depois de
        # uma reconexão NO MEIO do jogo (ver docstring lá: sem isso, o
        # cliente reconectava a CONTA mas nunca voltava a pedir o
        # personagem, e a sessão nova no servidor nunca virava uma
        # entidade real no mundo — AOI/combate nunca mais chegavam,
        # mesmo com o socket "conectado").
        self._selected_char_id = char.get("id", -1)
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

    def _handle_msg_auth_ok(self, payload: dict) -> None:
        """Bug real relatado pelo usuário (03/10/2026): quando a conexão
        cai NO MEIO do jogo, `client/network.py::NetworkClient` reconecta
        o socket e reenvia LOGIN sozinho (credenciais salvas) — o
        servidor autentica de novo e manda AUTH_OK, mas NADA reenviava
        SELECT_CHARACTER depois disso. Sem reselecionar, a sessão nova
        no servidor nunca virava uma entidade no mundo de novo — o
        cliente ficava com o socket "conectado" mas zero AOI_UPDATE/
        COMBAT_RESULT chegando (mob nunca mais agroava, outros players
        congelados), até o timeout de 30s de `game.py::run()` desistir e
        liberar a tela mesmo sem sincronizar nada de verdade ("entra no
        jogo com personagem padrão").

        AUTH_OK só chega aqui (via `_NET_DISPATCH_NAMES`, dispatch da
        gameplay) DEPOIS que o jogo já começou uma vez — o fluxo de
        login/seleção de personagem inicial usa seu próprio loop
        separado (`ui/login_screen.py`), nunca passa por
        `GameEngine`/`NetworkHandlers`. `self._selected_char_id` só
        existe depois do 1º `LOGIN_OK` (ver `_handle_msg_login_ok`), que
        é exatamente a garantia de que isto é uma reconexão NO MEIO do
        jogo, não o login normal.

        `_clear_remote_entity_caches()` (06/10/2026, 2ª parte do mesmo
        bug, achada depois — usuário relatou NPC/mob "travado" numa
        posição velha após reconectar): eid de entidade NÃO é estável
        entre reinícios do servidor (mundo reconstrói do zero, NPCs/mobs
        ganham eids NOVOS). Sem limpar os caches locais aqui, um
        WORLD_STATE novo (que chega logo depois do SELECT_CHARACTER
        acima) processa cada entidade via `_spawn_remote_mob` — que
        pula silenciosamente (`SPAWN_SKIP`, ver
        `client/remote_entity_handlers.py`) qualquer `server_eid` que já
        exista em `self._remote_mobs`, achando que já tem a entidade
        certa. Se um eid NOVO colidir por acaso com uma chave VELHA (de
        antes da reconexão), o spawn de verdade nunca acontece — o que
        fica na tela é a entidade ANTIGA (posição/eid de antes do
        restart), congelada pra sempre (NPC nunca mais se move por
        natureza; mob só se "autocorrige" se por acaso andar de novo,
        o que pode nunca acontecer). Mesmo método já usado por
        `_do_transition()` pra troca de mapa de verdade — reconexão é a
        MESMA classe de "o mundo do cliente não tem relação com o que
        vem a seguir", só que pro MESMO mapa."""
        self._remove_all_remote_entities(keep_tilemap=True)
        self._clear_remote_entity_caches()
        char_id = getattr(self, "_selected_char_id", -1)
        if char_id >= 0 and self._net:
            from shared.messages import MsgType as _MT_reauth
            print(f"[Client] reconectado — re-selecionando personagem {char_id}")
            self._net.send(_MT_reauth.SELECT_CHARACTER, {"char_id": char_id})

    def _handle_msg_world_state(self, payload: dict) -> None:
        for ent in payload.get("entities", []):
            eid  = ent.get("eid", -1)
            kind = ent.get("kind", "player")
            if kind == "harvestable":
                # Fase M1, revisão 2 (25/07/2026) — entidade real (posição+
                # aparência+loot, sem combate/diálogo). Conteúdo do loot em
                # si chega em seguida via LOOT_AVAILABLE (server/session.py::
                # _spawn_and_start), que só atualiza o Corpse já anexado
                # aqui — ver _handle_msg_loot_available.
                self._spawn_remote_harvestable(eid, ent)
                continue
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
                    "level":    ent.get("level", 1),
                    "kind":     kind,
                    **({"faction": ent["faction"]} if "faction" in ent else {}),
                    **({"target_eid": ent["target_eid"]} if "target_eid" in ent else {}),
                })

    def _handle_msg_entity_spawn(self, payload: dict) -> None:
        eid  = payload.get("eid", -1)
        kind = payload.get("kind", "player")
        # Verifica corpse ANTES do guard eid==-1 (corpse de mob morto usa
        # eid negativo — -1 inclusive — pra nunca colidir com eid real de
        # player/mob, ver server/session.py). Harvestable (Fase M1,
        # revisão 2, 25/07/2026) é uma entidade REAL agora, eid positivo
        # de verdade — cai no branch próprio abaixo, sem precisar do
        # tratamento especial de eid negativo que os corpses precisam.
        if kind == "corpse":
            corpse_id = -eid
            self._remote_corpses[corpse_id] = (payload.get("tx", 0), payload.get("ty", 0))
        elif kind == "harvestable":
            self._spawn_remote_harvestable(eid, payload)
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
                "level":    payload.get("level", 1),
                "kind":     kind,
                **({"faction": payload["faction"]} if "faction" in payload else {}),
                **({"light_active": True,
                    "light_item_id": payload.get("light_item_id", ""),
                    "light_intensity_pct": payload.get("light_intensity_pct", 0),
                    "light_radius_tiles": payload.get("light_radius_tiles", 0)}
                   if payload.get("light_active") else {}),
                # target_eid (07/10/2026, direção de player remoto parado)
                # — mesma classe de bug já documentada pra faction/luz
                # acima: cada handler reconstrói o dict de spawn na mão
                # com uma allowlist própria, precisando ser estendida UMA
                # POR UMA pra cada campo novo do servidor.
                **({"target_eid": payload["target_eid"]} if "target_eid" in payload else {}),
            })

    def _handle_msg_player_light_update(self, payload: dict) -> None:
        """Luz de item de bag acesa/apagada (23/09/2026) — alcança o dono E
        jogadores próximos numa mensagem só (_sessions_in_aoi inclui o
        dono sempre, ver server/session.py). Mesmo componente
        `PlayerLightSource` usado nos dois lados: entidade LOCAL (dono) ou
        entidade do jogador REMOTO — o loop de render (game.py) não
        distingue, só consulta quem tem o componente com active=True."""
        from engine.components import PlayerLightSource
        eid = payload.get("eid", -1)
        target = self.player_entity if eid == self._my_eid else self._remote_players.get(eid)
        if target is None:
            return
        pls = self.world.get_component(target, PlayerLightSource)
        if pls is None:
            pls = PlayerLightSource()
            self.world.add_component(target, pls)
        pls.active         = bool(payload.get("active", False))
        pls.item_id        = payload.get("item_id", "")
        pls.intensity_pct  = float(payload.get("intensity_pct", 0))
        pls.radius_tiles   = int(payload.get("radius_tiles", 0))

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
                from engine.components import SpellCast as _SCFail, CombatState as _CSFail, TileMovement as _TMFail
                _sc_fail = self.world.get_component(self.player_entity, _SCFail)
                if _sc_fail and _sc_fail.spell_id == sid:
                    self.world.remove_component(self.player_entity, _SCFail)
                _cs_fail = self.world.get_component(self.player_entity, _CSFail)
                if _cs_fail:
                    _cs_fail.is_casting = False
                # Servidor rejeitou o cast (19/10/2026) — mesma limpeza da
                # animação de windup que o cancelamento local por movimento já
                # faz (ui/spell_system.py::SpellCastSystem.update); sem isso o
                # personagem fica travado na pose de cast quando a rejeição
                # chega DEPOIS do cast_start já ter tocado (ex: alvo morreu
                # durante o cast_time). No-op seguro se não havia cast visual
                # ativo (anim_attack_state já vazio).
                _tm_fail = self.world.get_component(self.player_entity, _TMFail)
                if _tm_fail:
                    from engine.character_animation import clear_attack_anim_state as _caas_fail
                    _caas_fail(_tm_fail)
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
                from engine.effect_order import mark_activated as _mark_eo_fs
                _mark_eo_fs(self.world, self.player_entity, "escudo_fogo")

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
                from engine.effect_order import mark_activated as _mark_eo_ib
                _mark_eo_ib(self.world, self.player_entity, "bloco_de_gelo")
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
            from engine.entity_disguise import discover_sprite_variants as _disc_cam
            _params_cam = _SCCam.get("camuflagem", {}).get("params", {})
            _dur_cam    = _params_cam.get("duration",  5.0)
            _spd_cam    = _params_cam.get("speed_pct", 0.60)
            _cs_cam     = self.world.get_component(self.player_entity, _CSCam)
            _cst_cam    = self.world.get_component(self.player_entity, _CStCam)
            _tm_cam     = self.world.get_component(self.player_entity, _TMCam)
            if _cs_cam:
                _cs_cam.camouflage_timer  = _dur_cam
                _variants_cam = _disc_cam("camuflagem")
                _cs_cam.camouflage_object = _rand_cam.choice(_variants_cam) if _variants_cam else ""
                from engine.effect_order import mark_activated as _mark_eo_cam
                _mark_eo_cam(self.world, self.player_entity, "camuflagem")
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
                        # _launch_fireball devolve a entrada criada direto (15/09/2026,
                        # item 9) — não precisa mais buscar de volta via query ECS.
                        _bdf_entry = self._spell_cast_system._launch_fireball(
                            self.player_entity, _proj_loc)
                        if _bdf_entry is not None:
                            _bdf_entry.target_server_id = _proj_srv
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
        # O projétil já colidiu — não há flecha viva pra prender um deferred_result nela.
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
                        # Cada flecha envia PROJECTILE_HIT_CS → 1 dano separado por flecha
                        self._player_proj_system.spawn(
                            _pl_pos_fr.x, _pl_pos_fr.y, self.player_entity, _fr_loc_tgt,
                            spell_id="flecha_reiterada",
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
                        )

        # Picada de Escorpião is_completion: cria projétil visual.
        # Primeira (única) flecha envia PROJECTILE_HIT_CS ao colidir.
        _pe_srv_tgt = payload.get("projectile_target", -1)
        if (caster_eid == self._my_eid
                and sid == "picada_escorpiao"
                and payload.get("is_completion")
                and not payload.get("failed")
                and sid not in self._cancelled_spell_ids
                and _pe_srv_tgt != -1):
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
                    self._player_proj_system.spawn(
                        _pl_pos_pe.x, _pl_pos_pe.y, self.player_entity, _pe_loc,
                        spell_id         = "picada_escorpiao",
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
                    )

        # Tiro Repulsivo is_completion: cria projétil visual.
        # Knockback/stun aplicados pelo servidor após PROJECTILE_HIT_CS.
        _tr_srv_tgt = payload.get("projectile_target", -1)
        if (caster_eid == self._my_eid
                and sid == "tiro_repulsivo"
                and payload.get("is_completion")
                and not payload.get("failed")
                and sid not in self._cancelled_spell_ids
                and _tr_srv_tgt != -1):
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
                    self._player_proj_system.spawn(
                        _pl_pos_tr.x, _pl_pos_tr.y, self.player_entity, _tr_loc,
                        spell_id         = "tiro_repulsivo",
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
                    )

        # Tiro Múltiplo is_completion: 1 flecha por alvo selecionado pelo servidor
        # (cone de visão + range + talento, ver _complete_tiro_multiplo_cast) — não
        # mais dano instantâneo de área. Cada flecha tem seu próprio target_server_id
        # e envia PROJECTILE_HIT_CS independente ao colidir, igual às outras skills
        # de flecha (mesma mecânica das outras: 1 flecha visual por alvo).
        _tm_srv_tgts = payload.get("projectile_targets", [])
        if (caster_eid == self._my_eid
                and sid == "tiro_multiplo"
                and payload.get("is_completion")
                and not payload.get("failed")
                and sid not in self._cancelled_spell_ids
                and _tm_srv_tgts):
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
                self._player_proj_system.spawn(
                    _pl_pos_tm.x, _pl_pos_tm.y, self.player_entity, _tm_loc,
                    spell_id         = "tiro_multiplo",
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
                )

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

        # Animação de CAST (windup) e LANÇAMENTO de skill (15/10/2026,
        # pedido do usuário: "uma animação pro cast, outra pro
        # lançamento") — reaproveita os eventos de apresentação que já
        # existiam só pra som, já broadcast AOI pra caster local E
        # observadores. "cast_start" liga o windup em loop pela duração
        # de cast_time (`trigger_cast_anim`); "launch"/"impact"/"miss"
        # (terminal de QUALQUER skill com sid, projétil ou não — ver
        # server/skill_processor.py/spell_completion_processor.py) liga
        # o golpe de verdade (`trigger_attack_anim`, cascata já
        # existente de sid/skill/attack) — por isso `_apply_combat_result`
        # (COMBAT_RESULT) deixou de disparar golpe pra skills com sid,
        # só continua pra auto-attack puro (sid=""), senão duplicaria a
        # animação (uma vez aqui, outra no impacto real do projétil).
        from engine.components import TileMovement as _TMSfxAnim
        _caster_local_anim = self._resolve_local_eid(caster_eid)
        if _caster_local_anim != -1:
            _tm_sfx_anim = self.world.get_component(_caster_local_anim, _TMSfxAnim)
            _class_id_sfx_anim = self._resolve_player_class_id(_caster_local_anim)
            if _tm_sfx_anim is not None and _class_id_sfx_anim:
                import pygame as _pg_sfx_anim
                from engine.character_animation import trigger_cast_anim as _tca, trigger_attack_anim as _taa_sfx
                _now_sfx_anim = _pg_sfx_anim.time.get_ticks()
                if event == "cast_start":
                    _tca(_tm_sfx_anim, _class_id_sfx_anim, sid, _now_sfx_anim)
                elif event in ("launch", "impact", "miss"):
                    _taa_sfx(_tm_sfx_anim, _class_id_sfx_anim, sid, _now_sfx_anim,
                            missed=(event == "miss"))

        fx = _SC_sfx.get(sid, {}).get("effects", {}).get(event, {})
        snd  = fx.get("sound")   # som único → play_skill
        snds = fx.get("sounds")  # variações aleatórias → play_random
        if not snd and not snds:
            return
        if is_local:
            # skip_local_caster (ver skill_config.py, ex: nova_congelante):
            # o PRÓPRIO conjurador já ouviu esse som via previsão local
            # (ui/spell_system.py::SpellCastSystem._CAST_HANDLERS, tocado
            # quando o cast local completa) — tocar de novo aqui duplicava
            # (bug real relatado pelo usuário, 14/09/2026, ver
            # PROBLEMAS_ARQUITETURA.md §137). Outros players continuam
            # ouvindo normal (branch `else` abaixo, nunca afetado).
            if fx.get("skip_local_caster"):
                return
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

    def _resolve_player_class_id(self, local_eid: int) -> "str | None":
        """class_id de um player (local OU remoto) já resolvido pra eid
        local — local via `CharacterStats`, remoto via
        `RemoteControlled`. None se não for um eid de player conhecido.
        Ponto único (15/10/2026) — usado por qualquer trigger de
        animação de personagem que precise do class_id a partir de um
        eid já resolvido (golpe/cast)."""
        from engine.components import CharacterStats as _CS_rpc, RemoteControlled as _RC_rpc
        if local_eid == self.player_entity:
            cs = self.world.get_component(local_eid, _CS_rpc)
            return cs.class_id if cs else None
        rc = self.world.get_component(local_eid, _RC_rpc)
        return rc.class_id if rc else None

    def _spawn_bystander_projectile(self, sid: str, payload: dict) -> None:
        """Cria a flecha/bola cosmética que um espectador vê quando OUTRO player
        lança uma skill de projétil. Nunca envia PROJECTILE_HIT_CS (target_server_id
        = -2) — o dano desse caster já chega normalmente via SKILL_RESULT/COMBAT_RESULT,
        isto é 100% visual."""
        from engine.components import Position as _PosB
        caster_local = self._remote_players.get(payload.get("caster_eid", -1), -1)
        if caster_local == -1:
            return
        caster_pos = self.world.get_component(caster_local, _PosB)
        if not caster_pos:
            return

        if sid == "bola_de_fogo":
            tgt_local = self._resolve_local_eid(payload.get("target_eid", -1))
            if tgt_local != -1:
                # _launch_fireball devolve a entrada direto (15/09/2026, item 9)
                _bystander_entry = self._spell_cast_system._launch_fireball(caster_local, tgt_local)
                if _bystander_entry is not None:
                    _bystander_entry.target_server_id = -2  # cosmético: sem PROJECTILE_HIT_CS
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
            self._player_proj_system.spawn(
                caster_pos.x, caster_pos.y, caster_local, tgt_local,
                spell_id="arrow",
                speed=_speed, dmg_weapon_pct=1.0, dmg_sp_coeff=0.0, color=color,
                damage_type="physical", launch_delay=_delay, guaranteed_hit=True,
                target_last_x=tgt_pos.x, target_last_y=tgt_pos.y, target_server_id=-2,
            )

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
            # Corpse expirou ou foi saqueado — remove visual. Guard de
            # defesa (25/07/2026, mesmo sinal já usado em
            # _sync_local_corpse_after_take/LootSystem.render_world/
            # _draw_remote_corpses): harvestable (permanente) TEM
            # Renderable — nunca deveria ser removido aqui, mesmo se
            # algum broadcast futuro mandar despawn por engano pra ele
            # (a causa raiz de verdade — server/session.py::request_loot
            # mandando despawn genérico pra QUALQUER corpse esvaziado,
            # inclusive harvestable — já foi corrigida lá; este guard é
            # só a segunda camada, mesmo princípio do resto do arquivo).
            corpse_id = -eid
            loot_data = self._available_loot.get(corpse_id)
            if loot_data:
                from engine.components import Renderable as _RenDespawn
                local_c_eid = loot_data.get("local_eid")
                if local_c_eid is not None and \
                        self.world.get_component(local_c_eid, _RenDespawn) is not None:
                    return
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
            # Remove nó de zona de item esgotado (Fase "Zona de itens",
            # 25/07/2026) — diferente do harvestable de posição fixa (que
            # nunca despawna, só esvazia), um nó de zona SOME de verdade ao
            # esgotar, reaproveitando o mesmo ENTITY_DESPAWN genérico de mob.
            local_hv_eid = self._remote_harvestables.pop(eid, None)
            if local_hv_eid is not None:
                from engine.components import Harvestable as _HVdespawn
                hv = self.world.get_component(local_hv_eid, _HVdespawn)
                if hv is not None:
                    self._available_loot.pop(hv.corpse_id, None)
                try:
                    self.world.remove_entity(local_hv_eid)
                except Exception:
                    pass
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
                    from engine.components import Position as _PosD, NpcSounds as _MSD
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
                from debug.interceptar_debug import INTERCEPTAR_DBG as _IDBG_cli
                # Se cliente já está dashando para o mesmo tile (prediction correta), não interrompe
                if (getattr(player_tm, "is_dash", False) and
                        player_tm.target_tile_x == real_tx and
                        player_tm.target_tile_y == real_ty):
                    _IDBG_cli.log("DASH_KEEP", real_tx=real_tx, real_ty=real_ty,
                                  is_dash_corr=is_dash_corr, skill_rejected=skill_rejected)
                    pass  # animação em curso bate com posição do servidor — mantém
                elif is_dash_corr and not skill_rejected:
                    _corr_duration = payload.get("duration")
                    if _corr_duration is not None:
                        # Deslocamento forçado (ex: vítima de knockback): UM evento
                        # com posição final + duração explícita — preempta qualquer
                        # fila/dash em curso (servidor já decidiu tudo, cliente só
                        # interpola a tween confirmada, sem enfileirar passo a passo
                        # nem prever o resultado de um empurrão em si mesmo).
                        _IDBG_cli.log("DASH_FORCED", real_tx=real_tx, real_ty=real_ty,
                                      duration=_corr_duration)
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
                        _IDBG_cli.log("DASH_START", real_tx=real_tx, real_ty=real_ty,
                                      queued=True)
                        if (not self._self_move_queue
                                or self._self_move_queue[-1] != (real_tx, real_ty)):
                            self._self_move_queue.append((real_tx, real_ty))
                    else:
                        _IDBG_cli.log("DASH_START", real_tx=real_tx, real_ty=real_ty,
                                      queued=False)
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
                    _IDBG_cli.log("DESYNC_CORR", real_tx=real_tx, real_ty=real_ty,
                                  cur_tx=player_tm.current_tile_x, cur_ty=player_tm.current_tile_y,
                                  was_dashing=_was_dashing, is_moving=player_tm.is_moving,
                                  gap=_corr_gap, skill_rejected=skill_rejected,
                                  will_cancel=(_was_dashing or not player_tm.is_moving or _corr_gap > 1))
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
                        # Para o clique de chão pendente em vez de deixar
                        # recalcular e tentar o MESMO passo recusado de novo
                        # (06/10/2026, pedido do usuário — bug real: cliente
                        # ficava num loop visual, tentando o mesmo passo
                        # bloqueado a cada recalc de PlayerAutoMove.
                        # path_recalc_timer, levando uma correção igual a
                        # esta de novo, sem nunca progredir). O jogador
                        # precisa clicar de novo se ainda quiser ir até lá —
                        # mesmo reset que `_process_ground_move` já faz ao
                        # "chegar" no destino (ui/player_input_system.py).
                        from engine.components import PlayerAutoMove as _PAMCorr
                        _pam_corr = self.world.get_component(self.player_entity, _PAMCorr)
                        if _pam_corr is not None:
                            _pam_corr.ground_target = None
                            _pam_corr.active        = False
                            _pam_corr.path.clear()
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
        # Visão compartilhada de time (30/07/2026) — servidor manda
        # ally_vision_centers SÓ quando não-vazio (custo zero fora de
        # contexto de time); ausência não significa "ficou vazio agora",
        # só "nada mudou o suficiente pra mandar" — FogSystem já lida com
        # isso lendo o valor mais recente a cada tick, não precisa de um
        # "clear" explícito aqui (self-heals na troca de mapa, ver
        # FogOfWar.switch_map).
        avc = payload.get("ally_vision_centers")
        if avc is not None:
            from engine.components import FogOfWar as _FogAVC
            fog = self.world.get_component(self.player_entity, _FogAVC)
            if fog is not None:
                fog.ally_centers = [tuple(c) for c in avc]

        # Posição de companheiros de grupo no mapa/minimapa (06/10/2026,
        # pedido do usuário) — SUBSTITUI o dict inteiro (nunca merge),
        # igual o servidor recomputa do zero todo tick
        # (server/session.py::_build_update_for_session). Campo ausente
        # (fora de grupo/grupo de 1) limpa — ui/map_markers.py lê isto
        # direto, sem filtrar mapa (servidor já só manda quem está no
        # MESMO mapa que o destinatário).
        _pp = payload.get("party_positions")
        self._party_member_positions = (
            {p["eid"]: p for p in _pp} if _pp else {})

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
                elif kind == "harvestable":
                    # Fase M1, revisão 2 (25/07/2026) — faltava aqui: este
                    # loop (AOI_UPDATE "spawned", o caminho MAIS COMUM de
                    # descoberta — o player anda até perto) tem seu PRÓPRIO
                    # dispatch de kind, separado de _handle_msg_entity_spawn/
                    # _handle_msg_world_state (que já tratavam harvestable
                    # corretamente). Sem este branch, o harvestable caía no
                    # loop de baixo (fallback "player") e virava um jogador
                    # remoto fantasma — a entidade de verdade (com o Corpse
                    # que o clique direito precisa) nunca era criada. Bug
                    # real relatado pelo usuário: clicou na caixa, loot não
                    # apareceu.
                    self._spawn_remote_harvestable(eid, sp)
        for sp in payload.get("spawned", []):
            eid  = sp.get("eid", -1)
            kind = sp.get("kind", "player")
            # Ignora enemy/mob_projectile/harvestable (já processados acima)
            # e o próprio player.
            if eid != -1 and eid != self._my_eid \
                    and kind not in ("enemy", "mob_projectile", "harvestable"):
                self._spawn_remote_player_entity(eid, {
                    "tx": sp.get("tx", 0), "ty": sp.get("ty", 0),
                    "name":     sp.get("name", "?"),
                    "class_id": sp.get("class_id", "guerreiro"),
                    "hp":       sp.get("hp", 100),
                    "hp_max":   sp.get("hp_max", 100),
                    "level":    sp.get("level", 1),
                    "kind":     kind,
                    **({"faction": sp["faction"]} if "faction" in sp else {}),
                    # Luz de item já acesa (04/10/2026, bug real relatado pelo
                    # usuário: jogador remoto com lampião ligado "apagava" pro
                    # observador sempre que saía e voltava pro AOI, ou numa
                    # troca de mapa) — o servidor já manda esses 4 campos aqui
                    # (WorldServer.player_light_field, ver server/session.py
                    # ~linha 4169), mas este dict (o caminho MAIS COMUM de
                    # descoberta — "spawned" do AOI_UPDATE de cada tick) nunca
                    # os repassava pra _spawn_remote_player_entity, mesmo já
                    # existindo o mesmo tratamento em _handle_msg_entity_spawn
                    # (ENTITY_SPAWN, usado só por ENTRADAS pontuais fora do
                    # sweep por tick) — os 2 deveriam sempre ter paridade.
                    **({"light_active": True,
                        "light_item_id": sp.get("light_item_id", ""),
                        "light_intensity_pct": sp.get("light_intensity_pct", 0),
                        "light_radius_tiles": sp.get("light_radius_tiles", 0)}
                       if sp.get("light_active") else {}),
                    # target_eid (07/10/2026, direção de player remoto
                    # parado) — mesma paridade que falta pra faction/luz
                    # entre os 3 pontos (ver comentário acima).
                    **({"target_eid": sp["target_eid"]} if "target_eid" in sp else {}),
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
        # Alvo de ataque de mobs remotos (11/10/2026, ver
        # PROBLEMAS_ARQUITETURA.md §273) — mesmo motivo de rodar SEMPRE
        # (mesmo com dict vazio): limpa o alvo de um mob que parou de
        # atacar/perdeu o alvo neste tick, não só quando alguém ganha um
        # alvo novo.
        self._sync_mob_targets(payload.get("mob_targets", {}))
        # Combat ANTES de despawned: garante floating text do golpe fatal
        # antes do mob ser removido de _remote_mobs
        for cr in payload.get("combat", []):
            self._apply_combat_result(cr)
        _died_eids = set(payload.get("died_eids", []))
        for eid in payload.get("despawned", []):
            # Harvestable com trava de quest que fechou de novo (Fase M3,
            # bug real relatado pelo usuário 25/07/2026: completou a quest,
            # o objeto devia sumir de novo e não sumia) OU nó de zona
            # esgotado que chegou por este caminho em vez de ENTITY_DESPAWN
            # avulso — mesmo cleanup dos outros pontos que tratam
            # harvestable (_remote_harvestables/_available_loot), só que
            # este "despawned" é o campo da PRÓPRIA AOI_UPDATE, não uma
            # mensagem ENTITY_DESPAWN separada (_handle_msg_entity_despawn
            # já tem o guard de Renderable pro seu próprio caminho — aqui
            # o servidor já decidiu conscientemente que deve sumir, sem
            # ambiguidade, então remove direto).
            local_hv_eid = self._remote_harvestables.pop(eid, None)
            if local_hv_eid is not None:
                from engine.components import Harvestable as _HVaoi
                hv = self.world.get_component(local_hv_eid, _HVaoi)
                if hv is not None:
                    self._available_loot.pop(hv.corpse_id, None)
                try:
                    self.world.remove_entity(local_hv_eid)
                except Exception:
                    pass
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
                        from engine.components import Position as _PosD2, NpcSounds as _MSD2
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
                    self._despawn_mob_or_play_death(local_eid, eid, is_kill=_is_kill)

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
            _cons_item = payload.get("item_id")
            if _cons_item:
                if payload.get("consumable_ok"):
                    self._consumable_system._finalize_consumable(self.player_entity, _cons_item)
                    # Pergaminho de receita (débito A4, 11/08/2026 — bug
                    # real: scroll era consumido mas a receita nunca era
                    # aprendida online, ver PROBLEMAS_ARQUITETURA.md).
                    # Servidor já validou e aprendeu na LearnedRecipes DELE
                    # — aqui só espelha localmente, pra UI de crafting
                    # refletir sem precisar relogar.
                    _learned = payload.get("learned_recipe")
                    if _learned:
                        from engine.components import LearnedRecipes as _LRRec
                        from engine.stat_fns import learn_recipe as _learn_recipe_fn
                        _lr_rec = self.world.get_component(self.player_entity, _LRRec)
                        if _lr_rec is not None:
                            _learn_recipe_fn(_lr_rec, _learned)
                elif payload.get("consumable_rejected"):
                    from ui.floating_text import WARN as _WARN_cons
                    _cons_msgs = {
                        "hp_full":   "HP já está cheio",
                        "mana_full": "Mana já está cheia",
                        "in_combat": "Não pode usar em combate",
                    }
                    _WARN_cons.add(_cons_msgs.get(payload.get("reason", ""),
                                                  "Não foi possível usar o item"))
                if self._consumable_system.pending_item_id == _cons_item:
                    self._consumable_system.pending_item_id = ""

            # Confirmação de Recarregar/consumo de flecha — o servidor já
            # reabasteceu/gastou a aljava de verdade; aqui só espelhamos o
            # resultado real na bag/aljava LOCAIS. Sem isso, o cliente nunca
            # ficava sabendo do reload (arrow_count/munição da bag divergiam
            # do servidor pra sempre após o primeiro uso online — bug real
            # "aljava diz estar cheia mas não está"). Ver PROBLEMAS_ARQUITETURA.md.
            #
            # `quiver_item_id` (16/09/2026, §154, bug real relatado pelo
            # usuário: trocar de aljava logo após esvaziar a antiga fazia a
            # NOVA nascer com 0 flechas) — esta confirmação fica numa fila
            # (queue_stats_update, servidor) e pode chegar DEPOIS que o
            # jogador já trocou de aljava aqui (equipar é predição local
            # instantânea). Sem checar identidade, um `quiver_arrow_count`
            # gerado pra aljava ANTIGA era aplicado cegamente na aljava que
            # estiver no slot agora — nunca aplicar em cima de uma aljava
            # diferente da que gerou o valor; confirmação "órfã" é ignorada
            # (o próximo AUTO_ATTACK/Recarregar reconcilia do jeito certo).
            if "quiver_arrow_count" in payload:
                from engine.components import Equipment as _EqRec
                _eq_rec = self.world.get_component(self.player_entity, _EqRec)
                _quiver_rec = _eq_rec.slots.get("offhand") if _eq_rec else None
                from debug.archer_debug import ADBG_CLIENT as _ADBG_c_stats
                _ADBG_c_stats.log(
                    "ARROW", self.player_entity, -1,
                    event_src="stats_update",
                    payload_quiver_item_id=payload.get("quiver_item_id"),
                    payload_arrow_count=payload.get("quiver_arrow_count"),
                    local_quiver_item_id=(_quiver_rec.item_id if _quiver_rec else None),
                    local_quiver_obj_id=(id(_quiver_rec) if _quiver_rec else None),
                    local_arrow_count_before=(_quiver_rec.arrow_count if _quiver_rec else None),
                )
                if (_quiver_rec is not None
                        and _quiver_rec.item_id != payload.get("quiver_item_id")):
                    _ADBG_c_stats.log("BLOCK", self.player_entity, -1,
                                      reason="quiver_item_id_mismatch")
                    _quiver_rec = None
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
                _ammo_id_rec    = payload.get("ammo_id", "")
                _ammo_name_rec  = payload.get("ammo_name", "")
                _ammo_taken_rec = payload.get("ammo_taken", 0)
                if _ammo_name_rec and _ammo_taken_rec > 0:
                    from engine.components import Inventory as _InvRec
                    _inv_rec = self.world.get_component(self.player_entity, _InvRec)
                    if _inv_rec:
                        _ammo_item_rec = next(
                            (it for it in _inv_rec.items
                             if it is not None and it.item_id == _ammo_id_rec), None)
                        if _ammo_item_rec is not None:
                            # ABSOLUTO (ammo_new_stack), não "-= ammo_taken" —
                            # idempotente, mesmo padrão de quiver_arrow_count
                            # acima. O relativo era a causa real de "comprei
                            # 200 flechas, aljava de 75, sumiu 150 da bag"
                            # (bug 19/07/2026): _apply_recarregar já tinha
                            # deduzido localmente ANTES desta confirmação
                            # chegar (client/ui/spell_system.py, cast
                            # visual_only) — "-=" em cima do que já tinha sido
                            # deduzido duplicava o consumo. Esse fix client-
                            # side (parar de prever localmente) já resolve
                            # a causa raiz; isto aqui é defesa em profundidade
                            # — segura mesmo se esta confirmação for
                            # reprocessada por qualquer motivo.
                            _ammo_item_rec.stack = payload.get(
                                "ammo_new_stack", max(0, _ammo_item_rec.stack - _ammo_taken_rec))
                            if _ammo_item_rec.stack <= 0:
                                _inv_rec.items.remove(_ammo_item_rec)
                    # LOG só quando é reload de verdade (ammo_name/ammo_taken
                    # presentes) — este MESMO payload shape ("quiver_arrow_
                    # count") também é usado pra sincronizar o consumo de 1
                    # flecha por tiro de Picada de Escorpião/Flecha Reiterada/
                    # Tiro Repulsivo/Tiro Múltiplo (spell_completion_processor.
                    # py:~1101), sem ammo_name/ammo_taken — logar aqui sem
                    # esse gate spamaria "Aljava recarregada" a cada flechada.
                    if _quiver_rec is not None:
                        from ui.combat_log import LOG as _LOG_rec
                        _LOG_rec.add(
                            f"Aljava recarregada: {_quiver_rec.arrow_count}/{_quiver_rec.max_arrows}",
                            (180, 220, 100))

            # Gold — hoje só usado pela recompensa de quest (_handle_quest_turn_in
            # já persiste certo, mas sem isso o cliente só via o valor novo no
            # próximo relogin — bug real reportado por testers). Valor
            # ABSOLUTO (não delta), mesmo padrão de hp/hp_max abaixo.
            if "gold" in payload:
                from engine.components import Wallet as _WalletGold
                _wallet_gold = self.world.get_component(self.player_entity, _WalletGold)
                if _wallet_gold:
                    _wallet_gold.gold = payload["gold"]
            # Correio (25/09/2026) — indicador de correspondência não
            # reclamada. DIFERENTE do marcador de quest (que o cliente
            # calcula sozinho a partir de dado local): "tem correspondência"
            # só existe no banco, do lado do servidor, então chega só por
            # aqui (login, nova correspondência recebida online, claim/delete
            # — ver server/session.py/mail_processor.py).
            if "mail_unread" in payload:
                from ui.ui_components import MailUIState as _MailUIStateStats
                _mail_ui_stats = self.world.get_component(self.player_entity, _MailUIStateStats)
                if _mail_ui_stats:
                    _mail_ui_stats.unread_count = payload["mail_unread"]
            # "instance_gold_gained" (13/08/2026, pedido do usuário — loot
            # automático dentro da instância não tinha NENHUM feedback
            # visual de gold subindo). Só emitido por server/
            # instance_progression.py::grant_instance_gold — nunca colide
            # com o "gold" absoluto acima (que continua sendo a fonte de
            # verdade do total). Cor dourada padrão já usada em todo o
            # resto do jogo pra ouro (ui/systems.py, ui/quest_system.py).
            _inst_gold_gain = payload.get("instance_gold_gained", 0)
            if _inst_gold_gain > 0:
                from engine.components import Position as _PosGold
                from ui.floating_text import FLT as _FLT_igold
                _pos_igold = self.world.get_component(self.player_entity, _PosGold)
                if _pos_igold:
                    _FLT_igold.add(f"+{_inst_gold_gain}g", _pos_igold.x, _pos_igold.y - 20,
                                   (255, 215, 0), size="small",
                                   target_id=self.player_entity)

            cs = self.world.get_component(self.player_entity, CombatStats)
            if cs and "hp" in payload:
                cs.current_hp = payload["hp"]
            if cs and "hp_max" in payload:
                cs.max_hp = payload["hp_max"]
            from engine.components import CombatState as _CStBush
            cs_state = self.world.get_component(self.player_entity, _CStBush)
            if cs_state and "in_bush" in payload:
                cs_state.is_bush_hidden = payload["in_bush"]
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
            # "talent_allocated" (02/08/2026, pedido do usuário — pontos de
            # talento pareciam acumular mesmo sendo usados): substituição
            # COMPLETA da alocação (não merge) — sem isso, o TalentTree
            # LOCAL nunca soube que entrou/saiu da instância, e o painel de
            # talentos mostrava/mutava a árvore REAL (pré-instância)
            # enquanto o servidor validava contra a árvore da instância
            # (vazia) — mesmo espírito de inv_snapshot/equip_snapshot
            # acima. Só emitido por instance_progression.py::
            # enter_/exit_normalized_progression.
            if "talent_allocated" in payload:
                from engine.components import TalentTree as _TTAlloc
                _tt_alloc = self.world.get_component(self.player_entity, _TTAlloc)
                if _tt_alloc is not None:
                    _tt_alloc.allocated = dict(payload["talent_allocated"])
                    # Sem isso, só o dict `allocated` mudava — os EFEITOS
                    # derivados (cs_flags comportamentais, ex:
                    # ice_cast_time_reduction de Precisão Elemental, e
                    # modifiers/skills desbloqueadas por talento) continuavam
                    # com o valor de FORA da instância, porque só
                    # `apply_talent_effects()` (chamado normalmente ao gastar
                    # ponto de talento, `ui/talent_system.py`) recalcula isso
                    # a partir de `tt.allocated` — nunca acontece sozinho só
                    # por mudar o dict. Bug real relatado pelo usuário
                    # (14/09/2026): Nova Congelante devia ser instantânea
                    # dentro da BG (Precisão Elemental maxada na normalização
                    # de instância) mas continuava com 1s de cast visível —
                    # o efeito (skill em si) já disparava na hora porque o
                    # SERVIDOR recalcula certo (`WorldServer.
                    # _apply_talent_modifiers`); só a barra de cast do
                    # CLIENTE ficava presa no valor antigo. Ver
                    # PROBLEMAS_ARQUITETURA.md §136.
                    try:
                        self._talent_system.apply_talent_effects()
                    except Exception:
                        pass
            # "level" (01/08/2026, bug real relatado pelo usuário: level
            # do player não aparecia 1 no client depois de entrar na
            # progressão normalizada de instância). DIFERENTE do level-up
            # por XP acima (que deriva o level localmente via
            # process_levelups a partir de xp_gained) — este é um
            # OVERRIDE direto e autoritativo do servidor (usado por
            # server/instance_progression.py, que muda `level` sem
            # nenhuma XP "ganha" pra re-derivar) — aplica direto, sem
            # passar por process_levelups.
            _srv_level = payload.get("level")
            if _srv_level is not None:
                from engine.components import CharacterStats as _CSLvl
                _char_lvl = self.world.get_component(self.player_entity, _CSLvl)
                if _char_lvl:
                    _char_lvl.level = int(_srv_level)
            # "current_xp"/"xp_to_next_level" (13/08/2026, bug real relatado
            # pelo usuário: "a barra de xp está mostrando a xp de fora da
            # instância"). Mesmo espírito do "level" acima — OVERRIDE direto
            # e autoritativo, nunca somado (diferente do "xp_gained" lá em
            # cima, que é incremental e reusa a curva/cap do MUNDO REAL via
            # process_levelups — errado pra dentro da instância). Só emitido
            # por server/instance_progression.py (entrar/sair/ganhar XP/
            # subir de nível DENTRO da instância).
            if "current_xp" in payload:
                from engine.components import CharacterStats as _CSXp, Position as _PosXp
                _char_xp = self.world.get_component(self.player_entity, _CSXp)
                if _char_xp:
                    _char_xp.current_xp = int(payload["current_xp"])
                    if "xp_to_next_level" in payload:
                        _char_xp.xp_to_next_level = int(payload["xp_to_next_level"])
                # "instance_xp_gained" — só pra mostrar o texto flutuante
                # "+N XP" (mesmo visual do XP normal); NUNCA usado pra somar
                # em current_xp (o valor final já veio explícito acima).
                _inst_xp_gain = payload.get("instance_xp_gained", 0)
                if _inst_xp_gain > 0:
                    from ui.floating_text import FLT as _FLT_ixp
                    _pos_ixp = self.world.get_component(self.player_entity, _PosXp)
                    if _pos_ixp:
                        _FLT_ixp.add(f"+{_inst_xp_gain} XP", _pos_ixp.x, _pos_ixp.y - 20,
                                    (100, 255, 100), size="small",
                                    target_id=self.player_entity)
                # "instance_leveled_up" — mesmo som de level-up do mundo
                # real (engine/stats_system.py::process_levelups), tocado
                # aqui em vez de lá porque a instância nunca passa por
                # process_levelups (curva/cap errados pra ela).
                if payload.get("instance_leveled_up"):
                    from ui.combat_log import LOG as _LOG_ilvl
                    SOUNDS.play_ui("levelup")
                    _LOG_ilvl.add(f"Level up! Nível {payload.get('level', '?')} (instância).",
                                 (255, 200, 0))
            # Atributos brutos (02/08/2026, bug real relatado pelo usuário:
            # "comprei um item e o HP ficou 140/380" — 380 era o max_hp
            # REAL, fora da instância). Só emitido por
            # server/instance_progression.py::_push_stats_update (enter/
            # exit/level-up de instância) — mesmo espírito do
            # talent_allocated acima, mas pros 5 atributos que ALIMENTAM
            # o recálculo de CombatStats. Sem isso, CharacterStats LOCAL
            # nunca sabia do reset pro piso da instância — `hp`/`hp_max`
            # (aplicados acima) só sobrescreviam o RESULTADO uma vez; o
            # PRÓXIMO recálculo local (qualquer add_modifier/
            # remove_modifier — client/inventory_handlers.py::_equip_item,
            # chamado pelo auto-equip da compra de instância, é um desses
            # gatilhos) recomeça de `CombatStats.base_stamina`, que só
            # `apply_char_stats_to_combat` sabe reconstruir a partir de
            # CharacterStats/PermanentStats — sem os atributos certos
            # aqui, esse recálculo trazia de volta o max_hp REAL.
            if "vitality" in payload:
                from engine.components import (CharacterStats as _CSAttr,
                                        PermanentStats as _PermAttr)
                from engine.stats_system import apply_char_stats_to_combat as _apply_cstc, \
                    sync_attack_interval as _sync_ai
                _char_attr = self.world.get_component(self.player_entity, _CSAttr)
                if _char_attr is not None:
                    _char_attr.strength     = payload.get("strength", _char_attr.strength)
                    _char_attr.intelligence = payload.get("intelligence", _char_attr.intelligence)
                    _char_attr.agility      = payload.get("agility", _char_attr.agility)
                    _char_attr.vitality     = payload["vitality"]
                    _char_attr.defense      = payload.get("defense", _char_attr.defense)
                    if cs is not None:
                        # PermanentStats (bônus legado roguelike — ver
                        # docstring de InstanceProgressionSnapshot): o
                        # SERVIDOR zera a própria cópia ao entrar na
                        # instância (enter_normalized_progression), mas a
                        # cópia LOCAL do cliente nunca era avisada — um
                        # personagem estabelecido com bônus legado real
                        # (>0) vazava de volta pro recálculo aqui,
                        # somando em cima do atributo já normalizado
                        # (bug real, 03/08/2026: "comprei o Arco do
                        # Caçador e o HP foi pra 160, mas o item não dá
                        # atributo de vida nenhum" — não era o item, era
                        # o PermanentStats real vazando no recálculo
                        # disparado pelo equip). `InstanceInventoryUIState.
                        # active` é a MESMA flag "estou na instância" —
                        # usa o valor deste payload se vier explícito
                        # (enter/exit sempre mandam), senão o estado atual
                        # (level-up de instância não manda `in_instance`,
                        # mas o player continua lá dentro).
                        from ui.ui_components import InstanceInventoryUIState as _IIUSAttr
                        _iius_attr = self.world.get_component(self.player_entity, _IIUSAttr)
                        _in_instance_now = payload.get("in_instance")
                        if _in_instance_now is None and _iius_attr is not None:
                            _in_instance_now = _iius_attr.active
                        _perm_attr = (None if _in_instance_now
                                     else self.world.get_component(self.player_entity, _PermAttr))
                        _apply_cstc(_char_attr, cs, _perm_attr)
                        from engine.components import Equipment as _EqAttr
                        _sync_ai(cs, self.world.get_component(self.player_entity, _EqAttr))
            # Sync de Inventory/Equipment de instância (01/08/2026, pedido
            # do usuário — painel HUD dedicado de inventário de instância).
            # Só emitido por server/instance_progression.py::
            # enter_/exit_normalized_progression, nos 2 momentos em que o
            # servidor troca esses componentes de verdade (add_component) —
            # sem isso, a bag local do cliente nunca sabia da troca e um
            # item comprado dentro da instância era anexado ao Inventory
            # REAL (errado), às vezes silenciosamente descartado se a bag
            # real já estivesse cheia. Substituição COMPLETA (replace, não
            # merge incremental) — mesmo espírito de TRADE_STATE, nunca
            # otimista. Reconstrução via self._restore_item (04/10/2026 —
            # era self._item_from_data, mesmo formato "raw" de BUY_RESULT/
            # TRADE_STATE, até virar um bug real relatado pelo usuário:
            # lampião ligado entrando/saindo da BG "esquecia" que tinha
            # light_source — WorldServer._item_data_from_obj nunca serializa
            # esse campo [nem outros fora da lista fixa de atributos de
            # combate], então _item_from_data reconstrói um Item sem ele, o
            # ícone mostra "apagado" e o clique cai no fallback de equipar em
            # vez de alternar a luz. _restore_item tenta o CATÁLOGO primeiro
            # por item_id — pega qualquer campo novo/futuro de propósito,
            # sem precisar listar campo por campo aqui — e só cai pro
            # _item_from_data "raw" de sempre quando o item não existe no
            # catálogo local (item de loja/instância sem equivalente, mesmo
            # fallback de sempre, comportamento preservado).
            if "inv_snapshot" in payload:
                from engine.components import Inventory as _InvSync
                _inv_sync = self.world.get_component(self.player_entity, _InvSync)
                if _inv_sync is not None:
                    _inv_sync.items = [self._restore_item(d) for d in payload["inv_snapshot"]]
                    _inv_sync.max_slots = int(payload.get("inv_max_slots", _inv_sync.max_slots))
            if "equip_snapshot" in payload:
                from engine.components import Equipment as _EqSync
                _eq_sync = self.world.get_component(self.player_entity, _EqSync)
                if _eq_sync is not None:
                    _eq_sync.slots = {
                        slot: (self._restore_item(d) if d else None)
                        for slot, d in payload["equip_snapshot"].items()
                    }
                    # Bug real relatado pelo usuário (03/08/2026, "jungo"):
                    # comprar o Arco do Caçador (só crit_rating) fazia o HP
                    # saltar de 140 pra 160 — o print das Estatísticas
                    # mostrava "+20" de HP/Estamina "de Itens" com a bag de
                    # equipamento TOTALMENTE VAZIA, antes mesmo de comprar
                    # qualquer coisa. Causa raiz: trocar `Equipment.slots`
                    # aqui (armadura/arma REAIS → vazio, ao entrar na
                    # instância) nunca limpava os modifiers `source=
                    # "equipment"` correspondentes em `CombatStats.
                    # modifiers` — essas duas listas são independentes
                    # (Equipment é só "o que está no slot", CombatStats.
                    # modifiers é quem realmente alimenta o recálculo). Os
                    # modifiers REAIS (armadura da cabeça/peito/botas do
                    # personagem antes de entrar) continuavam lá, invisíveis
                    # até o PRÓXIMO recalculate_effective_stats (disparado
                    # por QUALQUER add_modifier/remove_modifier — equipar o
                    # arco novo, mesmo sem ele mesmo dar HP) reaplicar TUDO
                    # de novo, com o "hp_max" explícito do payload já
                    # sobrescrito por cima do valor errado. Fix: espelha
                    # exatamente o que o SERVIDOR já faz em WorldServer.
                    # _apply_equipment_modifiers — reconstrói os modifiers
                    # `source="equipment"` do zero, a partir do Equipment
                    # que ACABOU de ser trocado (vazio na instância, cheio
                    # fora dela).
                    if cs is not None:
                        from engine.components import Modifier as _ModEqSync
                        cs.modifiers = [m for m in cs.modifiers if m.source != "equipment"]
                        for _it_eq in _eq_sync.slots.values():
                            if _it_eq is None:
                                continue
                            for _mod_eq in _it_eq.modifiers:
                                cs.modifiers.append(_ModEqSync(
                                    _mod_eq.attribute, _mod_eq.value, _mod_eq.type,
                                    source="equipment"))
                        cs._recalculate_effective_stats()
            # skills_hotbar/learned_skill_ids (02/08/2026, pedido do usuário
            # — a barra de ações continuava mostrando as skills REAIS
            # dentro da instância, em vez de ir liberando conforme o
            # personagem sobe de level lá dentro). Reconstrução via
            # PlayerSkills._make_skill(skill_id, SKILL_CATALOG) — skill é
            # dado ESTÁTICO compartilhado, não precisa de payload rico
            # (diferente de item, que é dinâmico).
            #
            # Posição decidida 100% pelo CLIENTE (25/08/2026, pedido do
            # usuário — configurar uma skill dentro OU fora da BG é a MESMA
            # preferência, ver ui/skill_slot_prefs.py::preferred_slot_for_skill). O
            # payload do servidor (1:1 por índice, sem mudar protocolo) só
            # serve pra descobrir QUAIS skill_ids são novos — a posição que
            # ele sugere é ignorada. Slot já conhecido NUNCA é retocado por
            # este bloco de novo (nem objeto novo, nem realocação), não
            # importa o que o servidor mande no índice correspondente —
            # antes disso (03/08/2026→25/08/2026) a reconciliação era POR
            # SLOT/posição: preservava o objeto Skill vivo (cooldown/
            # charges) quando o skill_id não mudava NAQUELE índice, mas
            # ainda reagia a mudança de POSIÇÃO — reordenar a hotbar DENTRO
            # da instância (arrasto) e depois receber um skills_hotbar novo
            # (qualquer level-up seguinte, frequente na BG) fazia o slot
            # editado voltar pro arranjo antigo do servidor (bug real
            # relatado pelo usuário: "Interceptar voltava pro slot 3
            # sozinho"). Agora: skill que sumiu do payload é REMOVIDA da
            # hotbar (troca de contexto — ex: entrar na BG precisa limpar as
            # skills do mundo aberto, mesmo motivo do bug original de
            # 02/08/2026); skill NOVA usa a preferência do jogador (ou
            # primeiro slot vazio); skill já conhecida E ainda presente no
            # payload fica exatamente onde o jogador a colocou, pra sempre,
            # até ele mesmo mexer de novo (nunca perde o objeto Skill vivo —
            # cooldown/charges — só por reaparecer no mesmo payload).
            if "skills_hotbar" in payload:
                from content.skill_config import SKILL_CATALOG as _SCHb
                _ps_hb = self.world.get_component(self.player_entity, PlayerSkills)
                if _ps_hb is not None:
                    _payload_ids_hb = {sid for sid in payload["skills_hotbar"] if sid}
                    # Dentro da instância, só revela skill que faz parte da
                    # ordem curada da classe (25/08/2026, bug real: "Punho
                    # no Queixo" apareceu no lugar de Interceptar). Causa:
                    # server/combat_processor.py tem um mecanismo à parte
                    # (rastreio de carga do talento Cavaleiro) que insere
                    # essa skill no primeiro slot vazio de PlayerSkills.skills
                    # assim que o player acerta um golpe — nunca soube de
                    # INSTANCE_SKILL_UNLOCK_ORDER, e como a instância
                    # pré-aloca a árvore de talento no máximo desde a
                    # entrada, o flag que ativa isso já está ligado ANTES do
                    # 1º level-up de verdade — roubando o slot que a skill
                    # curada da vez (Interceptar) ia usar. Fora da instância
                    # o filtro não se aplica — Punho no Queixo é ambient
                    # real do build Cavaleiro, comportamento intacto.
                    #
                    # `in_instance` deste MESMO payload (não o valor ainda
                    # não-atualizado do componente) decide se o filtro
                    # aplica (01/09/2026, bug real relatado pelo usuário:
                    # skill real "Impacto", fora da ordem curada de
                    # guerreiro, sumia da hotbar ao SAIR da BG — não só
                    # com /ff, qualquer saída real, porque `exit_
                    # normalized_progression` sempre manda `in_instance:
                    # False` e `skills_hotbar` no MESMO STATS_UPDATE, e
                    # este bloco processa ANTES do bloco que atualiza
                    # `InstanceInventoryUIState.active` — mais abaixo
                    # nesta função —, então lia o flag ainda `True`
                    # (estado de ANTES de sair) e filtrava a lista real
                    # inteira pela curada por engano. MESMO padrão já usado
                    # acima pro bloco de atributos brutos — payload
                    # explícito primeiro, estado atual só como reserva
                    # (level-up de instância não manda `in_instance`, mas
                    # o player continua lá dentro).
                    from ui.ui_components import InstanceInventoryUIState as _IIUSHb
                    _iius_hb = self.world.get_component(self.player_entity, _IIUSHb)
                    _in_instance_hb = payload.get("in_instance")
                    _in_instance_src_dbg = "payload" if payload.get("in_instance") is not None else "component"
                    if _in_instance_hb is None and _iius_hb is not None:
                        _in_instance_hb = _iius_hb.active
                    if _in_instance_hb:
                        from content.skill_config import INSTANCE_SKILL_UNLOCK_ORDER as _ISUOHb
                        from engine.components import CharacterStats as _CSHbFilter
                        _char_hb = self.world.get_component(self.player_entity, _CSHbFilter)
                        _allowed_hb = set(_ISUOHb.get(_char_hb.class_id, [])) if _char_hb else set()
                        _payload_ids_hb &= _allowed_hb
                    from debug.hotbar_slot_debug import HSD, DBG_ENABLED as _HSD_ON
                    if _HSD_ON:
                        from ui.skill_slot_prefs import _char_name as _hsd_char_name
                        import config as _hsd_cfg
                        _hsd_prefs = (_hsd_cfg.load().get("characters", {})
                                              .get(_hsd_char_name(self.world, self.player_entity), {})
                                              .get("skill_slot_prefs", {}))
                        HSD.log("STATS_UPDATE_RAW",
                                raw=payload["skills_hotbar"], in_instance=_in_instance_hb,
                                in_instance_src=_in_instance_src_dbg, prefs=_hsd_prefs)
                    for _i_rm, _s_rm in enumerate(_ps_hb.skills):
                        if _s_rm is not None and _s_rm.skill_id not in _payload_ids_hb:
                            if _HSD_ON:
                                HSD.log("REMOVED", skill_id=_s_rm.skill_id, old_slot=_i_rm)
                            _ps_hb.skills[_i_rm] = None
                    _known_hb = {s.skill_id for s in _ps_hb.skills if s}
                    for sid in payload["skills_hotbar"]:  # preserva a ORDEM original
                        if not sid or sid not in _payload_ids_hb or sid in _known_hb:
                            continue
                        _new_sk_hb = PlayerSkills._make_skill(sid, _SCHb)
                        if _new_sk_hb is None:
                            continue
                        from ui.skill_slot_prefs import preferred_slot_for_skill as _psfs_hb
                        _idx_hb = _psfs_hb(self.world, self.player_entity, _ps_hb, sid)
                        if _idx_hb is not None:
                            _ps_hb.skills[_idx_hb] = _new_sk_hb
                            if _HSD_ON:
                                HSD.log("PLACED", skill_id=sid, slot=_idx_hb,
                                        saved_pref=_hsd_prefs.get(sid) if _HSD_ON else None)
                        elif _HSD_ON:
                            HSD.log("SKIPPED_NO_SLOT", skill_id=sid)
                        _known_hb.add(sid)
            if "learned_skill_ids" in payload:
                _ps_learned = self.world.get_component(self.player_entity, PlayerSkills)
                if _ps_learned is not None:
                    from debug.hotbar_slot_debug import HSD as _HSD_lrn, DBG_ENABLED as _HSD_ON_lrn
                    if _HSD_ON_lrn:
                        _HSD_lrn.log("LEARNED_SKILL_IDS_SET",
                                     new_value=sorted(payload["learned_skill_ids"]),
                                     old_value=sorted(_ps_learned.learned_skill_ids))
                    _ps_learned.learned_skill_ids = set(payload["learned_skill_ids"])
            if "inv_snapshot" in payload or "equip_snapshot" in payload:
                # Causa raiz real do bug "não consigo comprar na loja de
                # instância" (02/08/2026, relatado pelo usuário): o servidor
                # valida espaço de inventário em BUY_REQUEST usando
                # `session.last_client_payload["inventory"]` (server/
                # session.py::_handle_buy_request) — o último SAVE_STATE que
                # o cliente mandou, NÃO o Inventory ao vivo. Sem mandar um
                # SAVE_STATE logo após o swap acima, esse cache no servidor
                # continuava com a contagem da bag REAL (ex.: 18 itens),
                # enquanto `Inventory.max_slots` ao vivo já virou 6 — toda
                # compra caía em "inventory_full" (`inv_count(18) + 1 >
                # max_slots(6)`) mesmo com a bag da instância vazia. Mesmo
                # problema na saída (max_slots volta a ser grande, mas o
                # cache ainda reflete os 6 slots da instância — inofensivo
                # nesse sentido, mas mandar aqui também mantém tudo
                # consistente nos dois sentidos).
                self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142
            if "in_instance" in payload:
                from ui.ui_components import InstanceInventoryUIState as _IIUS
                _iius = self.world.get_component(self.player_entity, _IIUS)
                if _iius is not None:
                    _new_active = bool(payload["in_instance"])
                    # Barra de consumíveis (02/08/2026, pedido do usuário —
                    # não deveria mostrar os consumíveis de fora da
                    # instância) — ConsumableBar é 100% client-local (nunca
                    # existiu no servidor, config pessoal salva em
                    # config.json), então o snapshot/troca é feito aqui
                    # mesmo, gatilhado pela MESMA transição de in_instance
                    # que já troca Inventory/Equipment/TalentTree no
                    # servidor. Guarda em self._real_consumable_bar_slots
                    # (só existe durante a instância).
                    from engine.components import ConsumableBar as _CBInst
                    _cb_inst = self.world.get_component(self.player_entity, _CBInst)
                    if _cb_inst is not None:
                        if _new_active and not _iius.active:
                            self._real_consumable_bar_slots = list(_cb_inst.slots)
                            _cb_inst.slots = [None] * len(_cb_inst.slots)
                        elif not _new_active and _iius.active:
                            _real_slots = getattr(self, "_real_consumable_bar_slots", None)
                            if _real_slots is not None:
                                _cb_inst.slots = _real_slots
                            self._real_consumable_bar_slots = None
                    # HUD de Kills/Deaths/Farm/Gold (02/08/2026) — zera na
                    # entrada nova (False->True); os valores da partida
                    # anterior não têm por que sobreviver a uma reentrada.
                    if _new_active and not _iius.active:
                        _iius.match_kills = 0
                        _iius.match_deaths = 0
                        _iius.match_assists = 0
                        _iius.match_farm = 0
                        _iius.match_gold = 0
                    _iius.active = _new_active
            if "match_kills" in payload or "match_deaths" in payload \
               or "match_assists" in payload \
               or "match_farm" in payload or "match_gold" in payload:
                # Push ao vivo, dirty-check já feito no servidor
                # (debug_battleground.py::_tick_kda_hud) — valores absolutos,
                # não delta (servidor já calculou o delta contra o snapshot
                # de entrada).
                from ui.ui_components import InstanceInventoryUIState as _IIUSKda
                _iius_kda = self.world.get_component(self.player_entity, _IIUSKda)
                if _iius_kda is not None:
                    if "match_kills" in payload:
                        _iius_kda.match_kills = payload["match_kills"]
                    if "match_deaths" in payload:
                        _iius_kda.match_deaths = payload["match_deaths"]
                    if "match_assists" in payload:
                        _iius_kda.match_assists = payload["match_assists"]
                    if "match_farm" in payload:
                        _iius_kda.match_farm = payload["match_farm"]
                    if "match_gold" in payload:
                        _iius_kda.match_gold = payload["match_gold"]
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
                if "hp" in payload or "hp_max" in payload:
                    from debug.aoi_debug import AOI_DBG as _AOI_DBG_phs
                    _AOI_DBG_phs.log("PLAYER_HP_STATS", server_eid=eid,
                                     hp_before=rc.hp, hp_novo=payload.get("hp"),
                                     hp_max_novo=payload.get("hp_max"))
                if "hp" in payload:
                    _was_dead_hp_rc = rc.hp <= 0
                    rc.hp = payload["hp"]
                    if _was_dead_hp_rc and rc.hp > 0:
                        # Revive remoto (15/10/2026) — limpa a animação
                        # de morte presa (hold_last_frame), mesma
                        # disciplina do revive local (_handle_msg_
                        # player_revive).
                        from engine.components import TileMovement as _TMReviveRc
                        from engine.character_animation import clear_attack_anim_state as _caas_rc
                        _tm_revive_rc = self.world.get_component(local_eid, _TMReviveRc)
                        if _tm_revive_rc is not None:
                            _caas_rc(_tm_revive_rc)
                if "hp_max" in payload:
                    rc.hp_max = payload["hp_max"]
                # Bug real relatado pelo usuário 17/07/2026: nameplate de
                # player remoto travava no level de login (server/
                # world_server.py::_sync_player_hp_dirty agora inclui level
                # no mesmo broadcast de HP — level-up sempre muda max_hp).
                if "level" in payload:
                    rc.level = payload["level"]
                # target_eid (07/10/2026, direção de player remoto parado)
                # — _sync_player_hp_dirty manda SEMPRE (inclusive -1,
                # "alvo desmarcado") junto com hp/level nesse mesmo
                # broadcast. Resolve servidor→local igual em qualquer
                # outro ponto deste arquivo (_apply_combat_result) — não
                # resolvido (alvo ainda não conhecido por este cliente)
                # cai em -1, nunca um valor velho "preso".
                if "target_eid" in payload:
                    from engine.components import CombatState as _CStStats
                    cst = self.world.get_component(local_eid, _CStStats)
                    if cst is not None:
                        _target_server_su = payload["target_eid"]
                        _target_local_su = -1
                        # NUNCA "a.get(x) or b.get(x) or -1" -- eid local 0
                        # é falsy em Python, cairia pro fallback errado.
                        # -1 checado primeiro e à parte: "sem alvo" nunca
                        # pode coincidir com _my_eid (ex.: ainda não
                        # atribuído/placeholder -1 antes do login real).
                        if _target_server_su != -1:
                            if _target_server_su == self._my_eid:
                                _target_local_su = self.player_entity
                            elif _target_server_su in self._remote_players:
                                _target_local_su = self._remote_players[_target_server_su]
                            elif _target_server_su in self._remote_mobs:
                                _target_local_su = self._remote_mobs[_target_server_su]
                        cst.target_entity_id = _target_local_su
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
            gst.auto_respawn = payload.get("auto_respawn", False)
            gst.respawn_s    = payload.get("respawn_s", 0.0)
        combat_state = self.world.get_component(self.player_entity, CombatState)
        if combat_state:
            combat_state.target_entity_id = -1
            combat_state.is_pursuing      = False
            combat_state.is_alive         = False  # can_act()==False: bloqueia skills/ataques
        self._net_last_target = -1
        self._death_timer = 0.0
        # Animação de morte (15/10/2026, pedido do usuário) — liga no
        # exato momento em que o servidor confirma a morte, trava no
        # último frame (ver engine.character_animation.trigger_death_anim)
        # até _handle_msg_player_revive limpar.
        from engine.components import CharacterStats as _CSDeathAnim, TileMovement as _TMDeathAnim
        _char_death_anim = self.world.get_component(self.player_entity, _CSDeathAnim)
        _tm_death_anim = self.world.get_component(self.player_entity, _TMDeathAnim)
        if _char_death_anim and _tm_death_anim is not None:
            import pygame as _pg_death_anim
            from engine.character_animation import trigger_death_anim as _tda
            _tda(_tm_death_anim, _char_death_anim.class_id, _pg_death_anim.time.get_ticks())

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
            # Limpa a animação de morte (15/10/2026) — "death" trava no
            # último frame sozinha pra sempre (hold_last_frame), senão o
            # personagem revivido ficaria preso na pose de morto.
            from engine.character_animation import clear_attack_anim_state as _caas_rev
            _caas_rev(ptm)
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

        # Cancela movimento automático pendente (02/08/2026, bug real
        # relatado pelo usuário: reviver dentro do battleground de teste
        # fazia o personagem "andar sozinho" de volta pro tile onde
        # morreu) — se o player estava perseguindo/seguindo/andando até um
        # ground_target quando morreu, PlayerAutoMove nunca era limpo (só
        # TileMovement/GhostState/CombatState acima), então o path/
        # ground_target ANTIGO (perto de onde morreu) sobrevivia ao
        # teleporte de revive e retomava assim que o movimento voltava a
        # ser permitido. Mesmo padrão já usado ao aplicar CC (root/stun/
        # polymorph/disoriented, ver applied_effects acima).
        from engine.components import PlayerAutoMove as _PAMRev
        am = self.world.get_component(self.player_entity, _PAMRev)
        if am:
            am.active      = False
            am.path.clear()
            am.ground_target = None
            am.follow_eid   = -1
        self._net_last_target = -1
        self._death_timer = 0.0

    def _handle_msg_ghost_state(self, payload: dict) -> None:
        # Atualiza estado do espírito local (raio do corpo / timer do cemitério).
        from engine.components import GhostState, Position as _PosGS
        gst = self.world.get_component(self.player_entity, GhostState)
        if not gst:
            return
        _was_ghost_gs = gst.is_ghost
        gst.is_ghost        = payload.get("is_ghost", gst.is_ghost)
        gst.near_corpse     = payload.get("near_corpse", gst.near_corpse)
        gst.graveyard_timer = payload.get("graveyard_timer", gst.graveyard_timer)
        if gst.is_ghost and not _was_ghost_gs:
            # Espírito liberado (15/10/2026) — limpa a animação de morte
            # presa (hold_last_frame); o branch de render de fantasma
            # (ui/render_system.py) nem olha pra ela, mas limpar aqui
            # evita estado obsoleto sobrevivendo pro próximo ciclo de
            # vida (mesma disciplina do revive, ver
            # _handle_msg_player_revive).
            from engine.components import TileMovement as _TMGhostAnim
            from engine.character_animation import clear_attack_anim_state as _caas_ghost
            _tm_ghost_anim = self.world.get_component(self.player_entity, _TMGhostAnim)
            if _tm_ghost_anim is not None:
                _caas_ghost(_tm_ghost_anim)
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
        from engine.components import RemoteControlled, Renderable, TileMovement as _TMEntDeathAnim
        rc = self.world.get_component(local_eid, RemoteControlled)
        if rc:
            rc.hp = 0
        rnd = self.world.get_component(local_eid, Renderable)
        if rnd:
            rnd.color = tuple(int(c * 0.35) + 20 for c in rnd.color[:3])
        # Animação de morte (15/10/2026, pedido do usuário) — mesmo
        # gatilho do player local, só que pro espelho remoto, pra
        # qualquer observador por perto ver também.
        if rc:
            _tm_ent_death = self.world.get_component(local_eid, _TMEntDeathAnim)
            if _tm_ent_death is not None:
                import pygame as _pg_ent_death
                from engine.character_animation import trigger_death_anim as _tda_ent
                _tda_ent(_tm_ent_death, rc.class_id, _pg_ent_death.time.get_ticks())

    def _resolve_loot_item(self, item_data: dict):
        """Reconstrói 1 item de loot recebido do servidor — prefere
        `item_id` (débito C2, 10/08/2026, identidade estável), cai pro
        nome de exibição só quando ausente. Fase 4.7 (12/08/2026, ver
        PROBLEMAS_ARQUITETURA.md §39, achado investigando bug real do
        talento Reciclagem): antes, `_handle_msg_loot_available`
        reconstruía SÓ por nome (mecanismo legado, nunca migrado junto
        do resto do projeto) — qualquer item construído no servidor sem
        `item_id` (ex: flecha recuperada pela Reciclagem,
        `server_death_handler.py`) ficava com nome coincidindo ou não
        com o catálogo: quando não coincidia, sumia da lista de loot
        silenciosamente; quando coincidia, reconstruía com um item_id
        DIFERENTE do que o servidor tinha guardado no corpse — clicar
        pra saquear nunca encontrava o id certo, "Já foi saqueado"
        mesmo com o item intocado. Mesmo padrão que
        `WorldServer._reconstruct_item` já usava do lado servidor,
        agora também aqui (`content.item_table.resolve_item_by_id`/
        `resolve_item_by_name`, ponto único de verdade pros 2 lados)."""
        from content.item_table import resolve_item_by_id, resolve_item_by_name
        item_id   = item_data.get("item_id", "")
        item_name = item_data.get("name", "")
        candidate = resolve_item_by_id(item_id) if item_id else None
        if candidate is None:
            candidate = resolve_item_by_name(item_name)
        if candidate is not None:
            candidate.stack = item_data.get("stack", 1)
        return candidate

    def _handle_msg_loot_available(self, payload: dict) -> None:
        # Servidor concedeu loot ao player local.
        # Cria entidade Corpse no ECS local para o LootSystem offline
        # funcionar IDENTICAMENTE ao offline (modal, equip, coins, scroll).
        from engine.entity_factory import create_corpse
        from engine.tileset import TILE_SIZE as _TS
        corpse_id = payload.get("corpse_id", -1)
        coins     = payload.get("coins", 0)
        tx        = payload.get("tx", 0)
        ty        = payload.get("ty", 0)
        if corpse_id < 0:
            return
        # Harvestable (Fase M1, revisão 2, 25/07/2026): a entidade JÁ
        # existe (criada em _spawn_remote_harvestable, no ENTITY_SPAWN/
        # WORLD_STATE que chegou antes) — só atualiza o Corpse vazio que
        # ela já tem, em vez de criar uma SEGUNDA entidade (isso causaria
        # a sprite E uma elipse duplicada, exatamente o bug relatado).
        _existing_loot = self._available_loot.get(corpse_id)
        if _existing_loot is not None:
            from engine.components import Corpse as _CorpseUpd
            _corpse_comp = self.world.get_component(_existing_loot["local_eid"], _CorpseUpd)
            if _corpse_comp is not None:
                loot_items = []
                for item_data in payload.get("items", []):
                    candidate = self._resolve_loot_item(item_data)
                    if candidate is not None:
                        loot_items.append(candidate)
                _corpse_comp.loot  = loot_items
                _corpse_comp.coins = coins
            return
        # Ponte "qual mob morreu nesse tile" (11/10/2026, ver
        # RemoteEntityHandlers._despawn_mob_or_play_death) — consumida
        # pela coordenada ORIGINAL (antes do redirect abaixo, que já
        # usa a mesma convenção), descartada se mais velha que a janela
        # de vida (pacote de loot raríssimamente atrasa tanto; se
        # atrasar, cai no fallback de elipse de sempre, nunca erro).
        _death_mob_key = ""
        _death_facing  = "down"
        _death_info = self._pending_death_race_by_tile.pop((tx, ty), None)
        if _death_info is not None:
            import pygame as _pg_dmk
            if _pg_dmk.time.get_ticks() - _death_info["ts_ms"] <= self._DEATH_RACE_BRIDGE_TTL_MS:
                _death_mob_key = _death_info["mob_key"]
                _death_facing  = _death_info["facing"]
        # Se mob morreu commitado para o próximo tile, loot segue para lá.
        _redirect = self._pending_loot_redirect.pop((tx, ty), None)
        if _redirect:
            tx, ty = _redirect
            px = tx * _TS + _TS // 2
            py = ty * _TS + _TS // 2
        # Reconstrói objetos de item a partir dos dados serializados do
        # servidor — `_resolve_loot_item` (item_id primeiro, nome como
        # fallback) já cobre catálogo geral + forja + loja + quest, num
        # ponto único (Fase 4.7, 12/08/2026, ver §39).
        loot_items = []
        for item_data in payload.get("items", []):
            candidate = self._resolve_loot_item(item_data)
            if candidate is None:
                continue
            loot_items.append(candidate)
            # Reciclagem: única fonte de loot de ammo com stack>1 hoje —
            # mesmo aviso do offline (systems.py), aqui no momento do drop.
            if candidate.item_type == "ammo" and candidate.stack > 1:
                LOG.add(f"Reciclagem! {candidate.stack} flechas no loot.",
                        (180, 220, 120))
        # Cria entidade Corpse no ECS local — LootSystem offline lê daqui
        px = tx * _TS + _TS // 2
        py = ty * _TS + _TS // 2
        local_corpse_eid = create_corpse(self.world, px, py, loot_items, coins,
                                         decay_time=120.0,
                                         mob_key=_death_mob_key, facing=_death_facing)
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
            # Usa NpcSounds component se o mob estiver no AOI do cliente
            _ev_local = self._remote_mobs.get(_ev_seid)
            _ev_snd   = None
            if _ev_local is not None:
                from engine.components import NpcSounds as _MSev
                _ev_snd = self.world.get_component(_ev_local, _MSev)
            SOUNDS.play_mob_sounds_at(_ev_snd, "aggro",
                                      _ev_sx, _ev_sy, _elx, _ely, base=0.8,
                                      dedup_key=f"aggro_{_ev_seid}")

    def _grant_items_to_inventory(self, items: list, log_verb: str = "Coletado") -> bool:
        """Reconstrói e adiciona itens (dicts serializados por
        server/server_death_handler.py::_serialize_item — {name, stack,
        rarity, ...}) ao Inventory local. Fonte única usada por
        LOOT_RESULT (loot de corpse) e INVENTORY_UPDATE (recompensa de
        item de quest, 23/07/2026, ver server/session.py::
        _handle_quest_turn_in) — antes só existia inline no handler de
        loot. Retorna True se pelo menos 1 item foi de fato adicionado
        (bag cheia não conta)."""
        from engine.components import Inventory as _InvGr
        from content.item_table import ITEMS as _ItemTableGr
        from content.quests_data import QUEST_ITEMS as _QIGr
        from content.crafting_data import MATERIALS as _MatGr, RECIPES as _RecGr, RECIPE_ITEMS as _RIGr
        inv = self.world.get_component(self.player_entity, _InvGr)
        if not inv:
            return False
        granted_any = False
        for item_data in items:
            item_id   = item_data.get("item_id", "")
            item_name = item_data.get("name", "")
            obj = None
            if item_id:
                # item_id (débito C2, 10/08/2026) — O(1). item_table cobre
                # loot+loja; forja tem 3 catálogos PRÓPRIOS (materiais,
                # resultado craftado, pergaminho de receita — "recipe_"
                # prefixo, mesmo padrão de server/world_server.py::
                # _item_factory_by_id) — achado real 11/08/2026: item
                # craftado/material NUNCA resolvia aqui (`obj` ficava None,
                # item perdido em silêncio da cópia local), já que só
                # item_table/QUEST_ITEMS eram checados.
                _factory = _ItemTableGr.get(item_id) or _QIGr.get(item_id) or _MatGr.get(item_id)
                if _factory is None:
                    _recipe = _RecGr.get(item_id)
                    _factory = _recipe.get("result_factory") if _recipe else None
                if _factory is None and item_id.startswith("recipe_"):
                    _factory = _RIGr.get(item_id[len("recipe_"):])
                if _factory:
                    try:
                        obj = _factory()
                    except Exception:
                        obj = None
            else:
                # Save/payload sem item_id (legado) — varredura por nome.
                for _key, factory in _ItemTableGr.items():
                    try:
                        candidate = factory()
                    except Exception:
                        continue
                    if getattr(candidate, "name", "") == item_name:
                        obj = candidate
                        break
                if obj is None:
                    _qi_factory = _QIGr.get(item_name)
                    if _qi_factory:
                        try:
                            obj = _qi_factory()
                        except Exception:
                            obj = None
            if obj is None:
                continue
            obj.stack = item_data.get("stack", 1)
            # Tenta empilhar em stack existente (mesma lógica de
            # ui/systems.py::LootSystem._try_take_item)
            stacked = False
            if obj.max_stack > 1:
                for existing in inv.items:
                    if existing is not None and existing.item_id == obj.item_id \
                            and existing.stack < existing.max_stack:
                        existing.stack += obj.stack
                        stacked = True
                        break
            if not stacked:
                if len(inv.items) < inv.max_slots:
                    inv.items.append(obj)
                else:
                    LOG.add("Inventario cheio!", (255, 160, 0))
                    continue
            granted_any = True
            col = {"common": (200,200,200), "uncommon": (30,200,30),
                  "rare": (80,140,255), "epic": (180,50,255),
                  "legendary": (224,135,47), "mythic": (221,68,68)
                  }.get(getattr(obj, "rarity", "common"), (200, 200, 200))
            LOG.add(f"{log_verb}: {obj.name} ({getattr(obj, 'rarity', 'common')})", col)
        return granted_any

    def _handle_msg_inventory_update(self, payload: dict) -> None:
        """Item(ns) concedido(s) fora do fluxo normal de loot — hoje só a
        recompensa de item de quest (server/session.py::
        _handle_quest_turn_in, 23/07/2026). Mesmo mecanismo de
        reconstrução/stack de LOOT_RESULT, via _grant_items_to_inventory.

        "removed" (28/07/2026, bug real relatado pelo usuário: item de
        quest entregue ficava "fantasma" na bag) — itens que o servidor
        JÁ removeu do Inventory DELE pra objetivos collect_item ao
        entregar a quest (engine/quest_logic.py::complete_quest); aqui só
        espelha a mesma remoção na bag LOCAL, senão o cliente nunca fica
        sabendo e o item nunca some da tela."""
        items = payload.get("items", [])
        if self._grant_items_to_inventory(items, log_verb="Recompensa"):
            SOUNDS.play_ui("loot_item")
        removed = payload.get("removed", [])
        if removed:
            self._remove_items_from_inventory(removed)

    def _remove_items_from_inventory(self, removed: list) -> None:
        """Espelha client-side a remoção que engine/quest_logic.py::
        complete_quest já fez no Inventory do servidor ao entregar uma
        quest — `removed` é [{"item_id": str, "stack": int}, ...] (stack =
        quantidade a tirar, não a stack original do item; "item_id" desde
        10/08/2026, débito C2 — era "name" antes). Mesma lógica de
        redução/pop que complete_quest usa no lado servidor."""
        from engine.components import Inventory as _InvRm
        inv = self.world.get_component(self.player_entity, _InvRm)
        if not inv:
            return
        for entry in removed:
            item_id = entry.get("item_id", "")
            needed = entry.get("stack", 0)
            i = 0
            while i < len(inv.items) and needed > 0:
                item = inv.items[i]
                if item is None:
                    i += 1
                elif item.item_id == item_id:
                    if item.stack <= needed:
                        needed -= item.stack
                        inv.items.pop(i)
                    else:
                        item.stack -= needed
                        needed = 0
                        i += 1
                else:
                    i += 1

    def _handle_msg_skill_granted(self, payload: dict) -> None:
        """Skill concedida fora do fluxo normal de treinador — hoje só a
        recompensa de skill de quest (server/session.py::
        _handle_quest_turn_in, 25/07/2026). O servidor já gravou em
        learned_skill_ids do lado dele antes de mandar esta mensagem — aqui
        só materializa o mesmo localmente (mesma lógica de
        ui/trainer_system.py::_do_learn, sem custo/checagem de nível, já
        concedido pelo servidor)."""
        from engine.components import PlayerSkills as _PS_sg
        from content.skill_config import SKILL_CATALOG as _SC_sg
        skill_id = payload.get("skill_id", "")
        name     = payload.get("name", skill_id)
        ps = self.world.get_component(self.player_entity, _PS_sg)
        if not ps or skill_id in ps.learned_skill_ids:
            return
        ps.learned_skill_ids.add(skill_id)
        new_sk = _PS_sg._make_skill(skill_id, _SC_sg)
        if new_sk is not None:
            # Slot preferido do jogador primeiro (25/08/2026, ver
            # ui/skill_slot_prefs.py::preferred_slot_for_skill), fallback
            # pro primeiro slot vazio.
            from ui.skill_slot_prefs import preferred_slot_for_skill as _psfs_sg
            idx = _psfs_sg(self.world, self.player_entity, ps, skill_id)
            if idx is not None:
                ps.skills[idx] = new_sk
            else:
                ps.skills.append(new_sk)
        LOG.add(f"Aprendido: {name} (recompensa de quest)", (230, 210, 120))
        SOUNDS.play_ui("loot_item")

    _TRAIN_FAIL_REASONS = {
        "class":             "Classe errada para essa habilidade.",
        "locked":            None,  # sem mensagem (04/10/2026) — ver abaixo
        "already_learned":   None,  # no-op silencioso, já aprendida
        "invalid_trainer":   "Treinador inválido.",
        "invalid_skill":     "Habilidade inválida.",
        "no_player_skills":  "Erro ao treinar — tente novamente.",
        "not_logged_in":     None,
    }

    def _handle_msg_train_skill_result(self, payload: dict) -> None:
        """Resposta a TRAIN_SKILL_REQUEST (04/10/2026 — antes, treinar era
        100% client-trusted; `WorldServer.process_train_skill` agora é
        quem decide de verdade). `"reason": "locked"` não mostra
        mensagem nenhuma de propósito (pedido do usuário) — o player só
        vê o fluxo de quest normal (`ui/trainer_system.py::_open_menu`
        já prioriza quest completável/disponível sobre o menu de
        assunto, e `_compute_options` nem lista "Treinamento" enquanto
        travado — nunca chega a mandar este request pra começo). Os
        casos "level"/"insufficient_gold" já mostram feedback
        INSTANTÂNEO client-side antes de mandar o request
        (`_do_learn`) — só chegam aqui se alguém adulterar o cliente,
        daí a mensagem genérica de fallback."""
        if not payload.get("success"):
            reason = payload.get("reason", "")
            msg = self._TRAIN_FAIL_REASONS.get(reason, "Não foi possível aprender.")
            if msg:
                LOG.add(msg, (200, 60, 60))
            return

        from engine.components import PlayerSkills as _PS_tsr, Wallet as _W_tsr
        from content.skill_config import SKILL_CATALOG as _SC_tsr
        skill_id = payload.get("skill_id", "")
        name     = payload.get("name", skill_id)
        ps     = self.world.get_component(self.player_entity, _PS_tsr)
        wallet = self.world.get_component(self.player_entity, _W_tsr)
        if wallet is not None and "new_gold" in payload:
            wallet.gold = int(payload["new_gold"])
        if not ps or skill_id in ps.learned_skill_ids:
            return
        ps.learned_skill_ids.add(skill_id)
        new_sk = _PS_tsr._make_skill(skill_id, _SC_tsr)
        if new_sk is not None:
            from ui.skill_slot_prefs import preferred_slot_for_skill as _psfs_tsr
            idx = _psfs_tsr(self.world, self.player_entity, ps, skill_id)
            if idx is not None:
                ps.skills[idx] = new_sk
            else:
                ps.skills.append(new_sk)
        LOG.add(f"Aprendido: {name}", (220, 180, 50))
        SOUNDS.play_ui("loot_item")

    def _handle_msg_loot_result(self, payload: dict) -> None:
        """Resposta ao LOOT_REQUEST (ui/systems.py::LootSystem, via
        _online_loot_requester — bug real relatado pelo usuário
        17/07/2026: em grupo, cada membro processava sua PRÓPRIA cópia
        local do corpse [criada em LOOT_AVAILABLE] de forma independente,
        sem checar com o servidor — dois membros lootavam o MESMO ouro.
        Agora o crédito real só acontece AQUI, com o que o servidor
        confirma que ainda sobrava no corpse — se outro membro do grupo
        já pegou tudo, chega vazio (items=[], coins=0) e nada é creditado."""
        corpse_id = payload.get("corpse_id", -1)
        coins     = payload.get("coins", 0)
        items     = payload.get("items", [])
        reason    = payload.get("reason", "")

        if coins > 0:
            from engine.components import Wallet as _WalLr
            wallet = self.world.get_component(self.player_entity, _WalLr)
            if wallet:
                wallet.gold += coins
                LOG.add(f"+{coins} moedas coletadas!", (255, 215, 0))

        if items:
            self._grant_items_to_inventory(items)

        if coins == 0 and not items:
            # Vazio pode ser por 2 motivos bem diferentes (débito A4,
            # 11/08/2026): já foi saqueado por outro membro do grupo, OU o
            # item existia mas não coube na mochila (aí `reason` vem
            # preenchido — o item continua no corpse, não foi perdido).
            from ui.floating_text import WARN as _WarnLr
            if reason == "inventory_full":
                _WarnLr.add("Mochila cheia")
            else:
                _WarnLr.add("Já foi saqueado")

        if coins > 0 or items:
            SOUNDS.play_ui("loot_gold" if coins > 0 else "loot_item")

        # Atualiza a cópia LOCAL do corpse com só o que foi CONFIRMADO — e
        # só remove a entidade/fecha o modal quando fica REALMENTE vazia
        # (ver docstring de _sync_local_corpse_after_take).
        item_ids = [it.get("item_id", "") for it in items]
        self._sync_local_corpse_after_take(corpse_id, coins, item_ids)
        # Débito A4 (11/08/2026, ver PROBLEMAS_ARQUITETURA.md): saque
        # virou autoritativo no servidor — `request_loot()` já bota o item
        # direto no Inventory AO VIVO do servidor (o que está em `items`
        # aqui já é exatamente o que o servidor concedeu), então o
        # INV_SYNC que existia só pra avisar o servidor disso (e, de
        # carona, disparar `sync_collect_progress`) não faz mais falta —
        # o servidor já dispara esse progresso de quest ele mesmo, direto
        # em `request_loot`/`_handle_loot_request`.
        # Gold/itens mudaram — sincroniza save com o servidor
        self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142

    def _handle_msg_loot_update(self, payload: dict) -> None:
        """Outro membro do grupo sacou algo do MESMO corpse (LOOT_RESULT
        dele, server/session.py::_handle_loot_request) — sincroniza a
        cópia LOCAL sem creditar nada (quem recebe isto não pegou nada,
        só está sendo avisado que sumiu). Sem isso, cada membro do grupo
        só descobria a mudança ao CLICAR algo próprio — até lá via ouro/
        item "fantasma" já pego por outro, e clicar nele voltava vazio
        sem nunca corrigir a cópia local (bug real relatado pelo usuário
        17/07/2026: corpo "bugava e fechava" ao clicar no fantasma)."""
        corpse_id  = payload.get("corpse_id", -1)
        coins_taken = payload.get("coins_taken", 0)
        item_ids    = payload.get("item_ids_taken", [])
        self._sync_local_corpse_after_take(corpse_id, coins_taken, item_ids)

    def _sync_local_corpse_after_take(self, corpse_id: int, coins_taken: int,
                                      item_ids_taken: list) -> None:
        """Remove da cópia LOCAL do corpse (`Corpse` ECS) o que acabou de
        sair — meu próprio saque (_handle_msg_loot_result) ou o de outro
        membro do grupo (_handle_msg_loot_update). Só remove a entidade/
        fecha o modal quando fica REALMENTE vazia — sacar parcial mantém
        o resto visível/lootável (bug real relatado pelo usuário
        17/07/2026: sacar só o ouro removia o corpo inteiro da tela,
        levando junto os itens que ainda sobravam). `item_ids_taken`
        (débito A4, 11/08/2026, era `item_names_taken` antes) — nome de
        exibição não distingue itens diferentes com o mesmo nome."""
        loot_data = self._available_loot.get(corpse_id)
        if not loot_data:
            return
        local_eid = loot_data.get("local_eid")
        if local_eid is None:
            return
        self._loot_system._online_loot_pending.discard(local_eid)
        from engine.components import Corpse as _CorpseLr
        corpse_comp = self.world.get_component(local_eid, _CorpseLr)
        if corpse_comp is None:
            return
        if coins_taken > 0:
            corpse_comp.coins = 0
        for _iid in item_ids_taken:
            for _idx, _existing in enumerate(corpse_comp.loot):
                if getattr(_existing, "item_id", "") == _iid:
                    corpse_comp.loot.pop(_idx)
                    break
        still_has_loot = bool(corpse_comp.loot) or corpse_comp.coins > 0
        if not still_has_loot:
            # Harvestable (Fase M1, revisão 4, 25/07/2026) NUNCA deveria
            # sumir da tela ao esvaziar — é permanente (no_decay=True no
            # servidor), só o corpse de mob morto deveria desaparecer.
            # Bug real relatado pelo usuário: looteou a caixa, ela sumiu
            # SÓ pro personagem que looteou (o remove_entity abaixo é uma
            # remoção LOCAL, por cliente) — outro personagem, que nunca
            # chegou a esvaziar o pote comum pela própria conta, nunca
            # passou por este código e continuava vendo a caixa
            # normalmente. Detecta harvestable pelo mesmo sinal já usado
            # em LootSystem.render_world/_draw_remote_corpses: tem
            # Renderable (corpse de mob morto nunca tem).
            from engine.components import Renderable as _RenSync
            if self.world.get_component(local_eid, _RenSync) is not None:
                return
            self._available_loot.pop(corpse_id, None)
            self._remote_corpses.pop(corpse_id, None)
            try:
                self.world.remove_entity(local_eid)
            except Exception:
                pass

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
                            if ex_br and ex_br.item_id == item_br.item_id and \
                                    ex_br.stack < ex_br.max_stack:
                                ex_br.stack = min(ex_br.max_stack,
                                                  ex_br.stack + qty_br)
                                stacked = True
                                break
                    if not stacked and len(inv_br.items) < inv_br.max_slots:
                        item_br.stack = qty_br
                        inv_br.items.append(item_br)
                        from debug.archer_debug import ADBG_CLIENT as _ADBG_c_buy
                        _ADBG_c_buy.log("BUY", self.player_entity, -1,
                                        item_id=item_br.item_id, qty=qty_br,
                                        inv_len_after=len(inv_br.items),
                                        inv_item_ids_after=[getattr(i, "item_id", None) for i in inv_br.items])
                        # Auto-equipar na loja de instância (02/08/2026, pedido
                        # do usuário: "a ideia era os itens já valerem como
                        # equipados no momento que eu compro" — os 6 slots
                        # dedicados foram removidos por causa disso, a bag
                        # normal já basta). Só tenta se o item for
                        # equipável (tem slot de verdade, não consumível/
                        # material) — reaproveita self._equip_item
                        # (InventoryHandlers), que já valida classe/level/
                        # arma; se rejeitar, o item simplesmente fica na bag
                        # (mesmo fallback gracioso de uma compra fora da
                        # instância).
                        from ui.ui_components import InstanceInventoryUIState as _IIUSBuy
                        _iius_buy = self.world.get_component(self.player_entity, _IIUSBuy)
                        if (_iius_buy is not None and _iius_buy.active
                                and getattr(item_br, "slot", "")):
                            # Preserva a FRAÇÃO de HP através do equip
                            # (03/08/2026, pedido do usuário: "se o
                            # personagem está 100% de hp, quando ele
                            # equipa um item precisa continuar com 100%
                            # de hp, ele não sofreu nenhum dano") —
                            # _equip_item → add_modifier → recalculate_
                            # combat_stats SÓ preserva o HP ABSOLUTO
                            # (comportamento padrão de buff/debuff em
                            # qualquer RPG: current_hp não muda, então a
                            # % cai se max_hp subir) — comprar item na
                            # loja de instância não é dano nem cura, é
                            # normalização de equipamento; o jogador não
                            # deveria "perder" % de vida por isso.
                            from engine.components import CombatStats as _CSBuyHp
                            _cs_buy = self.world.get_component(self.player_entity, _CSBuyHp)
                            _hp_frac_buy = (_cs_buy.current_hp / _cs_buy.max_hp
                                           if _cs_buy and _cs_buy.max_hp > 0 else 1.0)
                            self._equip_item(item_br)
                            if _cs_buy is not None:
                                _cs_buy.current_hp = min(
                                    _cs_buy.max_hp, round(_hp_frac_buy * _cs_buy.max_hp))
                    # Auto-adiciona à barra de consumíveis na loja de
                    # instância (02/08/2026, pedido do usuário: "os
                    # consumíveis deveria aparecer só quando o player
                    # comprasse... então deveria ir para a barra de ações
                    # de consumíveis automaticamente"). Roda pra
                    # stacked OU não (item já existia na bag = ainda
                    # precisa aparecer na barra se ainda não estiver lá).
                    # Só o PRIMEIRO slot livre — não sobrescreve nada que
                    # o jogador já tenha colocado manualmente.
                    if getattr(item_br, "item_type", "") == "consumable":
                        from ui.ui_components import InstanceInventoryUIState as _IIUSCons
                        _iius_cons = self.world.get_component(self.player_entity, _IIUSCons)
                        if _iius_cons is not None and _iius_cons.active:
                            from engine.components import ConsumableBar as _CBBuy
                            _cb_buy = self.world.get_component(self.player_entity, _CBBuy)
                            if _cb_buy is not None and item_br.item_id not in _cb_buy.slots:
                                for _i_cb in range(len(_cb_buy.slots)):
                                    if _cb_buy.slots[_i_cb] is None:
                                        _cb_buy.slots[_i_cb] = item_br.item_id
                                        break
            from ui.combat_log import LOG as _LOG_BR
            price = payload.get("price", 0)
            name  = item_data.get("name", "item")
            _LOG_BR.add(f"Comprado: {name} por {price}g", (255, 215, 0))
            if getattr(self, "_shop_system", None) is not None:
                self._shop_system.note_transaction("buy")
            # Salva imediatamente — inventário e gold foram alterados server-side
            self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142
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
            if getattr(self, "_shop_system", None) is not None:
                self._shop_system.note_transaction("sell")
            self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142
        else:
            # Falha rara (sessão inválida etc.) — reverte gold local se servidor informou
            # o valor correto
            srv_gold = payload.get("new_gold")
            if srv_gold is not None and wal_sr is not None:
                wal_sr.gold = srv_gold
            from ui.floating_text import WARN as _WARN_SR
            _WARN_SR.add(payload.get("reason", "Venda recusada"))

    def _handle_msg_shop_undo_result(self, payload: dict) -> None:
        """Desfazer da loja (21/10/2026) — servidor já desfez na cópia
        dele; aqui espelha na local: compra desfeita tira o item e devolve
        o ouro, venda desfeita devolve o item e cobra o ouro."""
        from engine.components import Wallet as _WalSU
        from ui.floating_text import WARN as _WARN_SU
        from ui.combat_log import LOG as _LOG_SU
        self._shop_system.on_undo_result(int(payload.get("undo_left", 0)))
        if not payload.get("success"):
            _WARN_SU.add({
                "item_not_owned":    "Item não está mais na mochila",
                "insufficient_gold": "Ouro insuficiente para desfazer",
                "inventory_full":    "Mochila cheia",
            }.get(payload.get("reason", ""), "Nada para desfazer"))
            return
        wal = self.world.get_component(self.player_entity, _WalSU)
        if wal is not None:
            wal.gold = payload.get("new_gold", wal.gold)
        if payload.get("kind") == "buy":
            self._remove_items_from_inventory(payload.get("removed") or [])
            _LOG_SU.add("Compra desfeita", (180, 220, 100))
        elif payload.get("item"):
            self._grant_items_to_inventory([payload["item"]], log_verb="Venda desfeita")
        self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142

    # ── Correio (25/09/2026) ──────────────────────────────────────────
    # Entrega de item/gold chega por INVENTORY_UPDATE/STATS_UPDATE (canais
    # já existentes, tratados pelos handlers de sempre) — estes 4 só
    # atualizam o estado da UI de correio em si (MailUIState).

    def _handle_msg_ranking_result(self, payload: dict) -> None:
        category = payload.get("category", "")
        entries  = payload.get("entries", [])
        self._ranking_system.on_result(category, entries)

    def _handle_msg_mail_inbox_list(self, payload: dict) -> None:
        from ui.ui_components import MailUIState as _MailUIStateInbox
        mail_ui = self.world.get_component(self.player_entity, _MailUIStateInbox)
        if mail_ui is None:
            return
        mail_ui.inbox = list(payload.get("mails", []))
        if mail_ui.screen == "closed":
            mail_ui.screen = "inbox"

    def _handle_msg_mail_item_detail(self, payload: dict) -> None:
        from ui.ui_components import MailUIState as _MailUIStateDetail
        mail_ui = self.world.get_component(self.player_entity, _MailUIStateDetail)
        if mail_ui is None:
            return
        items = []
        for entry in payload.get("items", []):
            item_obj = self._item_from_data(entry.get("item")) if entry.get("item") else None
            items.append({"item_index": entry.get("item_index", 0), "item": item_obj,
                          "claimed": entry.get("claimed", False)})
        mail_ui.detail = {
            "mail_id":      payload.get("mail_id", -1),
            "sender_name":  payload.get("sender_name", ""),
            "subject":      payload.get("subject", ""),
            "body":         payload.get("body", ""),
            "items":        items,
            "gold":         payload.get("gold", 0),
            "gold_claimed": payload.get("gold_claimed", False),
        }
        mail_ui.screen = "detail"

    def _handle_msg_mail_send_result(self, payload: dict) -> None:
        from ui.combat_log import LOG as _LOG_MAIL
        from ui.floating_text import WARN as _WARN_MAIL
        from ui.ui_components import MailUIState as _MailUIStateSend
        mail_ui = self.world.get_component(self.player_entity, _MailUIStateSend)
        if payload.get("success"):
            _LOG_MAIL.add("Correspondencia enviada!", (180, 220, 100))
            if mail_ui is not None:
                mail_ui.compose_to = ""
                mail_ui.compose_subject = ""
                mail_ui.compose_subject_touched = False
                mail_ui.compose_body = ""
                mail_ui.compose_items = []
                mail_ui.compose_gold_text = ""
                mail_ui.screen = "inbox"
            self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142
        else:
            _WARN_MAIL.add(payload.get("reason", "Envio recusado"))

    def _handle_msg_mail_claim_result(self, payload: dict) -> None:
        from ui.ui_components import MailUIState as _MailUIStateClaim
        mail_ui = self.world.get_component(self.player_entity, _MailUIStateClaim)
        if not payload.get("success") or mail_ui is None or mail_ui.detail is None:
            return
        if mail_ui.detail.get("mail_id") != payload.get("mail_id"):
            return
        if "item_index" in payload and payload.get("item_index", -1) >= 0:
            for it in mail_ui.detail["items"]:
                if it["item_index"] == payload["item_index"]:
                    it["claimed"] = True
                    break
        if payload.get("gold_claimed"):
            mail_ui.detail["gold_claimed"] = True
        self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142

    def _handle_msg_craft_result(self, payload: dict) -> None:
        """Confirmação de CRAFT_REQUEST — débito A4 (11/08/2026, ver
        PROBLEMAS_ARQUITETURA.md). Servidor já descontou ouro/materiais e
        adicionou o resultado na SUA cópia; aqui só espelha isso na cópia
        local (mesmo padrão de BUY_RESULT — 1 clique, item cai direto na
        mochila, sem passo de "coletar")."""
        from engine.components import Wallet as _WalCR
        wal_cr = self.world.get_component(self.player_entity, _WalCR)
        _cs = getattr(self, "_crafting_system", None)
        if _cs is not None:
            _cs._frg_pending = False
        if payload.get("success"):
            if wal_cr is not None:
                wal_cr.gold = payload.get("new_gold", wal_cr.gold)
            # Materiais consumidos — bug real relatado pelo usuário
            # (11/08/2026): sem isso, a bag local nunca refletia o
            # material gasto (só sumia no próximo relog). Mesmo helper já
            # usado pra "removed" de INVENTORY_UPDATE (entrega de quest).
            materials_consumed = payload.get("materials_consumed") or []
            if materials_consumed:
                self._remove_items_from_inventory(materials_consumed)
            item = payload.get("item")
            if item:
                self._grant_items_to_inventory([item], log_verb="Forjado")
            if _cs is not None:
                _cs._frg_selected = None
                _cs._frg_data     = None
            self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142
        else:
            srv_gold = payload.get("new_gold")
            if srv_gold is not None and wal_cr is not None:
                wal_cr.gold = srv_gold
            from ui.floating_text import WARN as _WARN_CR
            _reason_msg = {
                "invalid_recipe":         "Receita inválida",
                "insufficient_gold":      "Ouro insuficiente",
                "insufficient_materials": "Materiais insuficientes",
                "inventory_full":         "Mochila cheia",
            }.get(payload.get("reason", ""), "Não foi possível forjar")
            _WARN_CR.add(_reason_msg)

    def _handle_msg_recycle_result(self, payload: dict) -> None:
        """Confirmação de RECYCLE_REQUEST — débito A4 (11/08/2026),
        contraparte de `_handle_msg_craft_result`. O item reciclado já
        saiu da cópia local no momento em que foi selecionado pro slot de
        reciclagem (`BlacksmithSystem._handle_bag_rclick`) — em rejeição,
        precisa voltar pra mochila local (servidor nunca tocou nele)."""
        from engine.components import Wallet as _WalRR, Inventory as _InvRR
        wal_rr = self.world.get_component(self.player_entity, _WalRR)
        _cs = getattr(self, "_crafting_system", None)
        if payload.get("success"):
            if wal_rr is not None:
                wal_rr.gold = payload.get("new_gold", wal_rr.gold)
            materials = payload.get("materials") or []
            if materials:
                self._grant_items_to_inventory(materials, log_verb="Reciclado")
            if _cs is not None:
                _cs._frg_pending = False  # no-op se não era forja, seguro
                _cs._rec_pending = False
                _cs._rec_item    = None
                _cs._rec_bag_idx = -1
        else:
            srv_gold = payload.get("new_gold")
            if srv_gold is not None and wal_rr is not None:
                wal_rr.gold = srv_gold
            if _cs is not None:
                _cs._rec_pending = False
                # Servidor recusou (ou nunca tocou no item) — devolve pra
                # mochila local, senão o item simplesmente desaparece da
                # bag do jogador sem nunca ter sido consumido de verdade.
                if _cs._rec_item is not None:
                    inv_rr = self.world.get_component(self.player_entity, _InvRR)
                    if inv_rr is not None:
                        _idx = max(0, min(_cs._rec_bag_idx, len(inv_rr.items)))
                        inv_rr.items.insert(_idx, _cs._rec_item)
                _cs._rec_item    = None
                _cs._rec_bag_idx = -1
            from ui.floating_text import WARN as _WARN_RR
            _reason_msg = {
                "invalid_item":      "Item inválido",
                "not_recyclable":    "Este item não pode ser reciclado",
                "insufficient_gold": "Ouro insuficiente",
            }.get(payload.get("reason", ""), "Não foi possível reciclar")
            _WARN_RR.add(_reason_msg)

    def _handle_msg_equip_rejected(self, payload: dict) -> None:
        """Servidor recusou o último EQUIP_ITEM (classe, level ou offhand
        travado — ver server/world_server.py::equip_item_from_inventory,
        único ponto que valida CLASS_ARMOR_ALLOWED/is_weapon_allowed_for_class/
        level_requirement de verdade, já que o check do cliente em
        _equip_item é só UX otimista).
        Reverte o slot local: tira o item que foi otimisticamente equipado e
        devolve pra bag (nunca perde o item). Débito A4 (10-11/08/2026) —
        cada EQUIP_ITEM/UNEQUIP_ITEM agora é atômico, então a reversão local
        já deixa cliente e servidor consistentes de novo, sem precisar
        remandar nada (o antigo _send_equip_sync() de recarga não existe
        mais)."""
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
            "offhand_locked": "mão dupla já equipada",
        }.get(reason, "requisito não atendido")
        from ui.floating_text import WARN as _WARN_EqR
        _WARN_EqR.add(f"Não foi possível equipar {item_name}: {_reason_msg}")

    def _handle_msg_error(self, payload: dict) -> None:
        """Erro genérico do servidor (`MsgType.ERROR`, `{"reason": ...}`)
        — usado por várias ações sem handler dedicado (ex: cooldown de
        `/unstuck`, bloqueio dentro de instância). Antes NUNCA tratado no
        cliente (11/10/2026, achado investigando PROBLEMAS_ARQUITETURA.md
        §274): a mensagem chegava e se perdia, a ação recusada parecia
        "não fez nada", sem explicação nenhuma pro jogador. `reason`
        desconhecida cai num fallback genérico — NUNCA mostra a string
        crua do servidor na tela."""
        reason = payload.get("reason", "")
        if reason.startswith("unstuck_cooldown:"):
            _secs = reason.split(":", 1)[1]
            msg = f"Aguarde {_secs}s pra usar 'Voltar ao Spawn' de novo"
        elif reason == "unstuck_in_instance":
            msg = "Não é possível voltar ao spawn dentro de uma instância"
        else:
            msg = "Ação não permitida no momento"
        from ui.floating_text import WARN as _WARN_ErrGen
        _WARN_ErrGen.add(msg)

    def _handle_msg_pong(self, payload: dict) -> None:
        if self._net:
            rtt = int(__import__("time").time() * 1000) - payload.get("client_ts", 0)
            self._net.latency_ms = rtt

    def _handle_msg_zone_change(self, payload: dict) -> None:
        """Servidor autorizou troca de mapa. Executa _do_transition no cliente."""
        map_file = payload.get("map_file", "")
        target_x = int(payload.get("target_x", 0))
        target_y = int(payload.get("target_y", 0))
        # Qualquer troca de mapa encerra o contexto de "time atual" (01/08/2026)
        # — remove o Faction real anexado por _handle_msg_arena_countdown
        # (client/arena_handlers.py) — sem isso, sair de uma instância
        # (arena/battleground) e voltar pro mundo aberto continuava
        # resolvendo hostilidade/cor de barra como se ainda estivesse no
        # time antigo. Reanexado de novo por ARENA_COUNTDOWN se entrar em
        # outra instância na sequência.
        from engine.components import Faction as _FacZc
        self.world.remove_component(self.player_entity, _FacZc)
        # Battleground de teste (02/08/2026): ZONE_CHANGE é o único sinal de
        # "saí da BG" (manual "/testbg leave" OU timeout automático de 15s
        # forçando saída) — fecha o modal de resultado se estiver aberto.
        self._bg_result_val = None
        # Fila REAL de Battleground (04/08/2026) — mesmo racional: sem
        # BG_MATCH_END dedicado (a fila real reaproveita ZONE_CHANGE puro
        # pra sair, ver server/bg_queue_processor.py::request_bg_leave),
        # então ZONE_CHANGE é o único sinal de "saí da partida" — limpa o
        # contexto de PvP local (client/bg_queue_handlers.py).
        self._bg_in_match_val         = False
        self._bg_opponents_server_val = set()
        # Câmera livre estilo MOBA (tecla Y, `Camera.free_mode`) só liga
        # dentro da BG/Arena e a re-trava é manual (apertar Y de novo,
        # ver game.py) — sem reset aqui, sair da instância com free_mode
        # ainda ligado deixava a câmera PRA SEMPRE travada na última
        # posição do pan de dentro da instância (coordenadas de um mapa
        # bem menor), sem chance de corrigir sozinho no mundo aberto (o
        # próprio toggle da tecla Y só funciona com a instância ainda
        # ativa) — bug real relatado pelo usuário 29/09/2026: mundo
        # preto/fora de lugar ao sair da BG, só resolvia relogando (ver
        # PROBLEMAS_ARQUITETURA.md §219). Reset incondicional aqui é
        # seguro mesmo fora de instância (free_mode já deveria ser False
        # ali de qualquer forma).
        from engine.components import Camera as _CamZc
        _cam_zc_first = self.world.get_first(_CamZc)
        if _cam_zc_first is not None:
            _cam_zc_first[1].free_mode = False
        # Cast de portal (01/10/2026): QUALQUER ZONE_CHANGE encerra um
        # cast de portal em andamento, se houver (conclusão natural —
        # chegamos ao destino escolhido) — mesmo princípio dos resets
        # acima (BG result/PvP/câmera livre). Bug real relatado pelo
        # usuário: a barra de cast só sumia depois que o jogador se
        # mexia, porque `_update_portal_state` só limpa
        # `_portal_cast_active` em 2 casos (movimento, PORTAL_CAST_CANCEL)
        # — a conclusão natural do cast nunca foi um desses casos, então
        # o ZONE_CHANGE chegava e a barra continuava lá até o primeiro
        # passo do jogador no mapa novo. `_cancel_portal_cast_local()` é
        # idempotente (no-op se não havia cast ativo).
        #
        # Captura ANTES de cancelar — "havia um cast de portal ativo
        # quando este ZONE_CHANGE chegou" é o único sinal client-side de
        # "este teleporte veio de um portal" (o payload não carrega
        # motivo nenhum) — consumido por `_trigger_portal_out_if_pending()`
        # (02/10/2026, animação de chegada "portal_out") dentro de
        # `_do_transition`, chamado logo abaixo.
        self._portal_arrival_pending = self._portal_cast_active is not None
        # stop_shake=False quando é conclusão NATURAL (chegada de
        # verdade) -- preserva a intensidade atual pra
        # _trigger_portal_out_if_pending() capturar e iniciar o fade de
        # chegada (ver docstring de _cancel_portal_cast_local). Sem
        # cast ativo (ZONE_CHANGE de caverna/morte), stop_shake=True é
        # no-op seguro (intensidade já era 0).
        self._cancel_portal_cast_local(stop_shake=not self._portal_arrival_pending)
        if map_file:
            # Tela de transição (28/09/2026, §215) — só quando é troca
            # de mapa DE VERDADE (_do_transition faz o mesmo check
            # internamente pro atalho de "morte no mesmo mapa"): sem
            # isso, um teleporte pro MESMO arquivo (portal de mundo
            # aberto) mostraria a tela por 1 frame sem necessidade —
            # esse caminho já é rápido (só reposiciona, não recarrega
            # nada) e nunca teve o engajo que motivou esta tela.
            _map_file_norm = map_file.replace("\\", "/")
            _is_real_change = _map_file_norm != self._current_map_file
            if _is_real_change:
                self._draw_map_transition_screen("Entrando em nova área...")
                self._display.blit(self.screen, (0, 0))
                import pygame as _pg_zc
                _pg_zc.display.flip()
                _pg_zc.event.pump()
                SOUNDS.set_game_ready(False)
            self._do_transition({
                "target_map": map_file,
                "target_x":   target_x,
                "target_y":   target_y,
            })
            if _is_real_change:
                SOUNDS.set_game_ready(True)

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
        verdade (nunca otimista — ver TradeUIState/plano do trade).

        Débito A4/fatiar-stack (11/08/2026, bug real relatado pelo
        usuário): o diff antigo comparava por CONTAGEM de entradas na
        oferta (multiset por nome) e removia o slot INTEIRO da bag por
        entrada nova — correto enquanto toda oferta movia o item
        INTEIRO, mas quebrado desde que passou a aceitar quantidade
        PARCIAL (Shift+clique, ver `server/trade_processor.py::
        add_trade_item`): ofertar 1 unidade de uma stack de 5 removia a
        STACK INTEIRA da bag (1 entrada nova contada = 1 slot inteiro
        popado, sem olhar quanto de fato saiu). Fix: diff por
        QUANTIDADE (soma de `stack` por item_id/nome), não por contagem
        de entradas — decrementa só o que realmente saiu da mochila, e
        devolve mesclando num stack existente quando possível."""
        tui = self._get_trade_ui()
        if tui is None or tui.trade_id != payload.get("trade_id", -1):
            return
        from collections import defaultdict
        from engine.components import Inventory as _InvTS, Wallet as _WalTS
        inv = self.world.get_component(self.player_entity, _InvTS)
        wal = self.world.get_component(self.player_entity, _WalTS)

        old_items = list(tui.my_offer)
        new_data  = payload.get("my_offer", [])

        def _key_obj(it):
            return getattr(it, "item_id", "") or getattr(it, "name", "")

        def _key_dict(d):
            return d.get("item_id", "") or d.get("name", "")

        old_totals = defaultdict(int)
        for it in old_items:
            old_totals[_key_obj(it)] += getattr(it, "stack", 1)
        new_totals = defaultdict(int)
        for d in new_data:
            new_totals[_key_dict(d)] += d.get("stack", 1)

        if inv is not None:
            for key in set(old_totals) | set(new_totals):
                delta = new_totals.get(key, 0) - old_totals.get(key, 0)
                if delta > 0:
                    # `delta` unidades A MAIS na oferta — saíram da bag agora.
                    remaining = delta
                    for idx in range(len(inv.items) - 1, -1, -1):
                        if remaining <= 0:
                            break
                        it = inv.items[idx]
                        if it is None or _key_obj(it) != key:
                            continue
                        take = min(remaining, it.stack)
                        it.stack -= take
                        remaining -= take
                        if it.stack <= 0:
                            inv.items.pop(idx)
                elif delta < 0:
                    # `-delta` unidades A MENOS na oferta — voltaram pra bag.
                    give = -delta
                    existing = next((it for it in inv.items
                                     if it is not None and _key_obj(it) == key
                                     and it.max_stack > 1 and it.stack < it.max_stack), None)
                    if existing is not None:
                        add = min(give, existing.max_stack - existing.stack)
                        existing.stack += add
                        give -= add
                    if give > 0 and len(inv.items) < inv.max_slots:
                        ref = next((it for it in old_items if _key_obj(it) == key), None)
                        if ref is not None:
                            import copy as _copy_ts
                            new_it = _copy_ts.copy(ref)
                            new_it.stack = give
                            inv.items.append(new_it)

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
        self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142

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
        self._send_save_state(include_fog=False)  # sem relacao com exploracao de mapa, ver #141/#142

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
        # Sussurro (02/10/2026, pedido do usuário): "to" só existe no eco
        # que volta pro PRÓPRIO remetente (server/session.py::_handle_chat)
        # -- rótulo e alvo de resposta (clique no nome, ver
        # client/chat_handlers.py::_chat_tab_lines) diferem pra cada lado,
        # já que "sender" aqui é sempre QUEM MANDOU, nunca o destino.
        whisper_to = payload.get("to")
        # reply_target é (mode, value) -- ("whisper", Nome) ou
        # ("party", None) -- ver client/chat_handlers.py::
        # _try_handle_chat_name_click pra o que cada um faz ao clicar.
        if channel == "whisper" and whisper_to:
            # "para Fulano" só, sem "Você sussurra" (03/10/2026, pedido
            # do usuário -- rótulo completo ficava grande demais no chat).
            label, reply_target = f"para {whisper_to}", ("whisper", whisper_to)
        elif channel == "whisper":
            label, reply_target = f"{sender} sussurra", ("whisper", sender)
        elif channel == "party":
            # "grupo/Fulano" (03/10/2026, pedido do usuário, formato
            # exato pedido) -- clicar liga o modo fixo de GRUPO, nunca
            # sussurra só pra quem mandou (resposta é pro grupo todo).
            label, reply_target = f"grupo/{sender}", ("party", None)
        else:
            label = sender
            reply_target = ("whisper", sender) if sender != "Sistema" else None
        # Decisão do usuário: sussurro cai na aba LOCAL mesmo (não ganha
        # aba própria), só com cor diferente (payload já manda o azul
        # claro, ver server/session.py) pra chamar atenção.
        target = self._chat_world if channel == "world" else self._chat_local
        target.append((label, text, color, reply_target))

        entity_id = self._resolve_chat_sender_entity(payload.get("eid", -1), sender)
        if entity_id != -1:
            from ui.chat_bubble import CHAT_BUBBLE
            CHAT_BUBBLE.add(entity_id, text)

    def _resolve_chat_sender_entity(self, sender_eid: int, sender_name: str) -> int:
        """Resolve o remetente pro eid LOCAL (próprio player ou player
        remoto) — usado só pra posicionar o balão de fala; se não achar
        (mob/NPC não manda chat, remetente já desconectou, ou mensagem de
        sistema sem eid), -1.

        Prioriza `sender_eid` (server_eid mandado em CHAT_MESSAGE desde
        21/07/2026) via `_resolve_local_eid` — o mesmo choke-point já usado
        por trade/duelo/party. Bug real corrigido: nome de personagem NÃO é
        único (duplicatas legadas — dois "Aventureiro" na mesma sessão),
        então resolver só por nome fazia o balão de fala aparecer sobre a
        cabeça do personagem ERRADO quando dois compartilhavam nome. O
        fallback por nome fica só para mensagens antigas/sem eid (ex:
        "Sistema" no anúncio de vitória de duelo, que não representa
        nenhuma entidade — nesse caso o fallback também não encontra nada,
        e está correto retornar -1)."""
        if sender_eid != -1:
            resolved = self._resolve_local_eid(sender_eid)
            if resolved != -1:
                return resolved
        if sender_name == getattr(self, "_logged_char_name", None):
            return self.player_entity
        return -1


# Dispatch O(1) de mensagem de rede -> NOME do handler (ver docstring
# de _handle_net_message acima) -- string + getattr(self, name), nunca
# referencia de funcao direto na classe: 17 desses handlers sao
# definidos em mixins IRMAOS de NetworkHandlers (DuelHandlers/
# PartyHandlers/ArenaHandlers/BattlegroundHandlers/BgQueueHandlers,
# ver game.py::GameEngine), so existem no MRO de `self` em runtime.
NetworkHandlers._NET_DISPATCH_NAMES = {
    MsgType.LOGIN_OK: "_handle_msg_login_ok",
    MsgType.LOGIN_ERROR: "_handle_msg_login_error",
    MsgType.AUTH_OK: "_handle_msg_auth_ok",
    MsgType.WORLD_STATE: "_handle_msg_world_state",
    MsgType.ENTITY_SPAWN: "_handle_msg_entity_spawn",
    MsgType.PLAYER_LIGHT_UPDATE: "_handle_msg_player_light_update",
    MsgType.COMBAT_RESULT: "_handle_msg_combat_result",
    MsgType.SKILL_RESULT: "_handle_msg_skill_result",
    MsgType.SKILL_EFFECT: "_handle_msg_skill_effect",
    MsgType.ENTITY_DESPAWN: "_handle_msg_entity_despawn",
    MsgType.ENTITY_MOVE: "_handle_msg_entity_move",
    MsgType.AOI_UPDATE: "_handle_msg_aoi_update",
    MsgType.STATS_UPDATE: "_handle_msg_stats_update",
    MsgType.SKILL_LEVELS_UPDATE: "_handle_msg_skill_levels_update",
    MsgType.CHAR_STATS_DATA: "_handle_msg_char_stats_data",
    MsgType.QUEST_UPDATE: "_handle_msg_quest_update",
    MsgType.PLAYER_DEATH: "_handle_msg_player_death",
    MsgType.PLAYER_REVIVE: "_handle_msg_player_revive",
    MsgType.GHOST_STATE: "_handle_msg_ghost_state",
    MsgType.ENTITY_DEATH: "_handle_msg_entity_death",
    MsgType.LOOT_AVAILABLE: "_handle_msg_loot_available",
    MsgType.SOUND_EVENT: "_handle_msg_sound_event",
    MsgType.LOOT_RESULT: "_handle_msg_loot_result",
    MsgType.LOOT_UPDATE: "_handle_msg_loot_update",
    MsgType.INVENTORY_UPDATE: "_handle_msg_inventory_update",
    MsgType.SKILL_GRANTED: "_handle_msg_skill_granted",
    MsgType.BUY_RESULT: "_handle_msg_buy_result",
    MsgType.SELL_RESULT: "_handle_msg_sell_result",
    MsgType.SHOP_UNDO_RESULT: "_handle_msg_shop_undo_result",
    MsgType.RANKING_RESULT: "_handle_msg_ranking_result",
    MsgType.MAIL_INBOX_LIST: "_handle_msg_mail_inbox_list",
    MsgType.MAIL_ITEM_DETAIL: "_handle_msg_mail_item_detail",
    MsgType.MAIL_SEND_RESULT: "_handle_msg_mail_send_result",
    MsgType.MAIL_CLAIM_RESULT: "_handle_msg_mail_claim_result",
    MsgType.CRAFT_RESULT: "_handle_msg_craft_result",
    MsgType.RECYCLE_RESULT: "_handle_msg_recycle_result",
    MsgType.TRAIN_SKILL_RESULT: "_handle_msg_train_skill_result",
    MsgType.ERROR: "_handle_msg_error",
    MsgType.PONG: "_handle_msg_pong",
    MsgType.ZONE_CHANGE: "_handle_msg_zone_change",
    MsgType.PORTAL_CAST_START: "_handle_msg_portal_cast_start",
    MsgType.PORTAL_CAST_CANCEL: "_handle_msg_portal_cast_cancel",
    MsgType.PORTAL_CAST_BEGIN: "_handle_msg_portal_cast_begin",
    MsgType.PORTAL_CAST_ABORT: "_handle_msg_portal_cast_abort",
    MsgType.PORTAL_ANIM_START: "_handle_msg_portal_anim_start",
    MsgType.PORTAL_ANIM_STOP: "_handle_msg_portal_anim_stop",
    MsgType.PORTAL_ANIM_ARRIVE: "_handle_msg_portal_anim_arrive",
    MsgType.EQUIP_REJECTED: "_handle_msg_equip_rejected",
    MsgType.TRADE_INVITE: "_handle_msg_trade_invite",
    MsgType.TRADE_OPEN: "_handle_msg_trade_open",
    MsgType.TRADE_STATE: "_handle_msg_trade_state",
    MsgType.TRADE_RESULT: "_handle_msg_trade_result",
    MsgType.TRADE_CANCELLED: "_handle_msg_trade_cancelled",
    MsgType.DUEL_INVITE: "_handle_msg_duel_invite",
    MsgType.DUEL_START: "_handle_msg_duel_start",
    MsgType.DUEL_END: "_handle_msg_duel_end",
    MsgType.PARTY_INVITE_RECEIVED: "_handle_msg_party_invite_received",
    MsgType.PARTY_INVITE_FAILED: "_handle_msg_party_invite_failed",
    MsgType.PARTY_STATE: "_handle_msg_party_state",
    MsgType.ARENA_QUEUE_STATE: "_handle_msg_arena_queue_state",
    MsgType.ARENA_MATCH_FOUND: "_handle_msg_arena_match_found",
    MsgType.ARENA_COUNTDOWN: "_handle_msg_arena_countdown",
    MsgType.ARENA_MATCH_START: "_handle_msg_arena_match_start",
    MsgType.ARENA_MATCH_END: "_handle_msg_arena_match_end",
    MsgType.ARENA_MATCH_RESULT: "_handle_msg_arena_match_result",
    MsgType.ARENA_GATE_OPEN: "_handle_msg_arena_gate_open",
    MsgType.BG_MATCH_RESULT: "_handle_msg_bg_match_result",
    MsgType.BG_QUEUE_STATE: "_handle_msg_bg_queue_state",
    MsgType.BG_MATCH_FOUND: "_handle_msg_bg_match_found",
    MsgType.BG_MATCH_START: "_handle_msg_bg_match_start",
    MsgType.CHAT_MESSAGE: "_handle_msg_chat_message",
}
