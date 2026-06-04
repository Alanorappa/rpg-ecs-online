"""
server/session.py
Gerencia sessões de jogadores conectados via WebSocket.

Implementa AOI subscription por sessão: cada Session mantém known_eids
(conjunto de entidades que o cliente já sabe sobre). Isso garante que:
- Mobs que entram no FOV após o login aparecem via ENTITY_SPAWN
- Mobs que saem do FOV são despawnados no cliente
- ENTITY_MOVE só é enviado para entidades já conhecidas
"""
from __future__ import annotations
import asyncio
import time

from shared.messages import MsgType, encode, decode
from shared.constants import AOI_RADIUS, PROTOCOL_VERSION, TICK_RATE
from utils import in_aoi as _in_aoi, SpatialHash as _SpatialHash


class Session:
    def __init__(self, ws, session_id: str):
        self.ws            = ws
        self.session_id    = session_id
        self.entity_id     = -1
        self.username      = ""
        self.char_data     = {}
        self.authenticated = False
        self._seq          = 0
        self.known_eids: set[int] = set()   # entidades que este cliente conhece
        # Último payload SAVE_STATE recebido do cliente (inventory/equipment/talents/skills/gold)
        self.last_client_payload: dict = {}

    async def send(self, msg_type: MsgType, payload: dict) -> None:
        try:
            self._seq += 1
            await self.ws.send(encode(msg_type, payload, seq=self._seq))
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"Session({self.username!r} eid={self.entity_id})"


class SessionManager:

    def __init__(self, world_server):
        self.world_server = world_server
        self._sessions:   dict[str, Session] = {}
        self._eid_to_sid: dict[int, str]     = {}
        self.world_server.register_on_tick(self._on_tick)

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    async def on_connect(self, ws, session_id: str) -> Session:
        session = Session(ws, session_id)
        self._sessions[session_id] = session
        print(f"[Session] +connect {session_id}  total={len(self._sessions)}")
        return session

    @staticmethod
    def _build_save_merge(srv_data: dict, client_payload: dict) -> dict:
        """
        Constrói o dict merged para save_character.
        Regras de autoridade:
        - Posição (tile_x, tile_y), hp, mp: servidor autoritativo
        - gold: SERVIDOR autoritativo (Wallet.gold no ECS — atualizado por compras/vendas/loot)
        - max_hp: cliente autoritativo se > 0 (inclui bônus de equipamento)
        - inventory, equipment, talents: cliente se disponível, None = não sobrescreve DB
        - skills: cliente se disponível, fallback srv_data
        - stats base (level, xp, attrs): servidor
        """
        client_p  = client_payload
        srv_stats = srv_data.get("stats", {})
        cli_stats = client_p.get("stats", {}) if client_p else {}
        merged_stats = dict(srv_stats)
        # gold: cliente autoritativo via SAVE_STATE.
        # _on_loot_collected garante que SAVE_STATE é disparado na ação de loot,
        # então o valor do cliente já inclui moedas recém-coletadas.
        _cli_gold = cli_stats.get("gold", 0)
        _srv_gold = srv_stats.get("gold", 0)
        merged_stats["gold"] = _cli_gold if _cli_gold > 0 else _srv_gold
        # max_hp: cliente autoritativo (inclui bônus de equipamento)
        _cli_mhp = cli_stats.get("max_hp", 0)
        if _cli_mhp > 0:
            merged_stats["max_hp"] = _cli_mhp
        client_skills = (client_p.get("skills") or None) if client_p else None

        # Fog: union de tiles explorados (servidor DB + cliente atual).
        # O cliente sempre envia o conjunto completo (recebeu o fog do servidor
        # no LOGIN_OK e acumulou novas descobertas), portanto a autoridade é
        # do cliente — mas fazemos union para segurança em caso de múltiplos logins.
        import json as _json
        _srv_fog_raw = srv_data.get("fog_json", "{}")
        try:
            _srv_fog = _json.loads(_srv_fog_raw) if isinstance(_srv_fog_raw, str) else (_srv_fog_raw or {})
        except Exception:
            _srv_fog = {}
        _cli_fog  = (client_p.get("fog") or {}) if client_p else {}
        if _cli_fog:
            merged_fog: dict = dict(_srv_fog)
            for _mk, _coords in _cli_fog.items():
                _mk = _mk.replace("\\", "/")
                _srv_set = {tuple(t) for t in merged_fog.get(_mk, [])}
                _cli_set = {tuple(t) for t in _coords}
                merged_fog[_mk] = [list(t) for t in (_srv_set | _cli_set)]
        else:
            merged_fog = _srv_fog

        return {
            "tile_x":    srv_data.get("tile_x", 10),
            "tile_y":    srv_data.get("tile_y", 10),
            "hp":        min(srv_data.get("hp", 100), merged_stats.get("max_hp", 9999)),
            "mp":        srv_data.get("mp", 100),
            "stats":     merged_stats,
            "inventory": client_p.get("inventory") if client_p else None,
            "equipment": client_p.get("equipment") if client_p else None,
            "talents":   client_p.get("talents")   if client_p else None,
            "skills":    client_skills if client_skills else (srv_data.get("skills") or None),
            "fog":       merged_fog if merged_fog else None,
        }

    async def on_disconnect(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        if session.entity_id != -1:
            # Salva ANTES de remover a entidade do ECS
            if session.authenticated and session.char_data.get("id"):
                from server.auth import save_character
                srv_data = self.world_server.get_player_save_data(session_id)
                if srv_data:
                    merged = self._build_save_merge(srv_data, session.last_client_payload)
                    try:
                        await save_character(session.char_data["id"], merged)
                        print(f"[Session] saved {session.username!r}  "
                              f"tile=({merged['tile_x']},{merged['tile_y']})  "
                              f"hp={merged['hp']}  gold={merged['stats'].get('gold', 0)}")
                    except Exception as e:
                        print(f"[Session] ERRO ao salvar {session.username!r}: {e}")
            self._eid_to_sid.pop(session.entity_id, None)
            eid = session.entity_id
            for other in self._sessions.values():
                other.known_eids.discard(eid)
            await self._broadcast_all(MsgType.ENTITY_DESPAWN, {"eid": eid})
            self.world_server.despawn_player(session_id)
        print(f"[Session] -disconnect {session.username!r}")

    async def on_message(self, session: Session, raw: str) -> None:
        try:
            msg_type, payload, seq, ts = decode(raw)
        except ValueError as e:
            await session.send(MsgType.ERROR, {"reason": str(e)})
            return
        handler = self._handlers.get(msg_type)
        if handler:
            try:
                await handler(self, session, payload, ts)
            except Exception as e:
                import traceback
                print(f"[Session] ERRO em handler {msg_type}: {e}")
                traceback.print_exc()
                await session.send(MsgType.ERROR, {"reason": f"server_error:{type(e).__name__}"})

    # ── Handlers C→S ─────────────────────────────────────────────────────────

    async def _handle_login(self, session: Session, payload: dict, ts: int) -> None:
        if payload.get("version", 0) != PROTOCOL_VERSION:
            await session.send(MsgType.LOGIN_ERROR, {"reason": "version_mismatch"})
            return
        from server.auth import authenticate
        username = payload.get("username", "")
        password = payload.get("password", "")

        char_data = await authenticate(username, password)
        if char_data is None:
            await session.send(MsgType.LOGIN_ERROR, {"reason": "invalid_credentials"})
            return

        for s in self._sessions.values():
            if s.authenticated and s.username == username:
                await session.send(MsgType.LOGIN_ERROR, {"reason": "already_online"})
                return

        # Inclui stats enviados pelo cliente no char_data para spawn_player usar
        char_data["client_ap"]     = payload.get("ap",     0.0)
        char_data["client_max_hp"] = payload.get("max_hp", 0)
        eid = self.world_server.spawn_player(session.session_id, char_data)

        session.username       = username
        session.entity_id      = eid
        session.char_data      = dict(char_data)
        session.authenticated  = True
        self._eid_to_sid[eid]  = session.session_id

        tx, ty = self.world_server.get_tile_pos(session.session_id)

        # HP autoritativo do servidor para o cliente sincronizar
        srv_hp, srv_hp_max = self.world_server.get_player_hp(session.session_id)
        await session.send(MsgType.LOGIN_OK, {
            "token":     session.session_id,
            "eid":       eid,
            "char":      dict(char_data),
            "server_ts": int(time.time() * 1000),
            "hp":        srv_hp,
            "hp_max":    srv_hp_max,
        })

        # Snapshot inicial: jogadores próximos
        near_players = self.world_server.get_players_in_aoi(session.session_id, AOI_RADIUS)
        for p in near_players:
            s2 = self._sessions.get(p.get("session_id", ""))
            if s2:
                _s2_hp, _s2_hp_max = self.world_server.get_player_hp(s2.session_id)
                p["name"]     = s2.username
                p["class_id"] = s2.char_data.get("class_id", "guerreiro")
                p["hp"]       = _s2_hp
                p["hp_max"]   = _s2_hp_max
                p["level"]    = s2.char_data.get("level", 1)
                p["effects"]  = []

        # Snapshot inicial: mobs próximos
        near_mobs = self.world_server.get_mobs_in_aoi(tx, ty, AOI_RADIUS)

        all_entities = near_players + near_mobs
        await session.send(MsgType.WORLD_STATE, {
            "tick":     self.world_server.tick_count,
            "tx":       tx, "ty": ty,
            "entities": all_entities,
        })

        # Popula known_eids com tudo que foi enviado no WORLD_STATE
        session.known_eids.add(eid)  # próprio player
        for ent in all_entities:
            session.known_eids.add(ent["eid"])

        # Avisa outros que este player entrou
        _new_hp, _new_hp_max = self.world_server.get_player_hp(session.session_id)
        spawn_payload = {
            "eid":      eid, "kind":     "player",
            "tx":       tx,  "ty":       ty,
            "name":     username,
            "class_id": char_data.get("class_id", "guerreiro"),
            "hp":       _new_hp,
            "hp_max":   _new_hp_max,
            "level":    char_data.get("level", 1),
            "effects":  [],
        }
        await self._broadcast_aoi_except(session, MsgType.ENTITY_SPAWN, spawn_payload)
        # Outros players passam a conhecer este
        for s in self._sessions.values():
            if s.authenticated and s.session_id != session.session_id:
                sx, sy = self.world_server.get_tile_pos(s.session_id)
                if _in_aoi(tx, ty, sx, sy, AOI_RADIUS):
                    s.known_eids.add(eid)

        print(f"[Session] login ok: {username!r}  eid={eid}  tile=({tx},{ty})")

    async def _handle_move(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        tx = int(payload.get("tx", 0))
        ty = int(payload.get("ty", 0))
        accepted = self.world_server.move_player(session.session_id, tx, ty)
        if accepted:
            await self._broadcast_aoi_from_session(session, MsgType.ENTITY_MOVE, {
                "eid":     session.entity_id,
                "tx":      tx, "ty": ty,
                "from_tx": payload.get("from_tx", tx),
                "from_ty": payload.get("from_ty", ty),
            })
        else:
            real_tx, real_ty = self.world_server.get_tile_pos(session.session_id)
            await session.send(MsgType.ENTITY_MOVE, {
                "eid":     session.entity_id,
                "tx":      real_tx, "ty": real_ty,
                "from_tx": real_tx, "from_ty": real_ty,
            })

    async def _handle_logout(self, session: Session, payload: dict, ts: int) -> None:
        """Cliente pediu desconexão graciosa — salva e remove a sessão."""
        if session.authenticated:
            await self.on_disconnect(session.session_id)

    async def _handle_ping(self, session: Session, payload: dict, ts: int) -> None:
        await session.send(MsgType.PONG, {
            "client_ts": payload.get("client_ts", 0),
            "server_ts": int(time.time() * 1000),
        })

    async def _handle_auto_attack(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        target_eid = int(payload.get("tid", -1))
        self.world_server.set_player_target(session.session_id, target_eid)
        # Clique direito num mob → enter_combat imediatamente (systems.py:1244)
        # Impede HP5 regen antes do primeiro hit, igual ao offline
        if target_eid != -1:
            from components import CombatState as _CS
            from stat_fns import enter_combat as _ec
            player_eid = session.entity_id
            pcst = self.world_server.world.get_component(player_eid, _CS)
            if pcst:
                _ec(pcst)
                pcst.is_pursuing = True   # espelha o clique direito do cliente
        else:
            from components import CombatState as _CS_stop
            player_eid = session.entity_id
            pcst = self.world_server.world.get_component(player_eid, _CS_stop)
            if pcst:
                pcst.is_pursuing = False

    async def _handle_projectile_hit(self, session: Session, payload: dict, ts: int) -> None:
        """Projétil do player colidiu com o alvo — aplica dano no servidor."""
        if not session.authenticated:
            return
        spell_id  = payload.get("spell_id", "")
        target_id = int(payload.get("target_id", -1))
        player_eid = self.world_server.get_entity_id(session.session_id)
        if not spell_id or target_id == -1 or player_eid == -1:
            return
        self.world_server._apply_spell_on_projectile_hit(player_eid, spell_id, target_id)

    async def _handle_cancel_cast(self, session: Session, payload: dict, ts: int) -> None:
        """Player cancelou cast por movimento — remove da fila de completions do servidor."""
        if not session.authenticated:
            return
        sid = payload.get("sid", "")
        if not sid:
            return
        player_eid = self.world_server.get_entity_id(session.session_id)
        # Remove entradas pendentes (timer ainda correndo)
        self.world_server._pending_spell_completions = [
            e for e in self.world_server._pending_spell_completions
            if not (e["player_eid"] == player_eid and e["spell_id"] == sid)
        ]
        # Remove entradas já em voo (timer expirou, aguardando PROJECTILE_HIT_CS).
        # Sem isso, cast cancelado no último frame ainda causa dano quando o projétil
        # visual (criado por is_completion) chega ao alvo e envia PROJECTILE_HIT_CS.
        self.world_server._spells_in_flight_queue = [
            e for e in self.world_server._spells_in_flight_queue
            if not (e["player_eid"] == player_eid and e["spell_id"] == sid)
        ]
        # Channeling cancelado (ex: Calamidade Flamejante interrompida por movimento):
        # remove o componente Channeling do player para parar os ticks de dano no servidor.
        from components import Channeling as _ChanCancel
        _ch_comp = self.world_server.world.get_component(player_eid, _ChanCancel)
        if _ch_comp and _ch_comp.spell_id == sid:
            try:
                self.world_server.world.remove_component(player_eid, _ChanCancel)
            except Exception:
                pass
            from components import CombatState as _CStCancel
            _cst_cancel = self.world_server.world.get_component(player_eid, _CStCancel)
            if _cst_cancel:
                _cst_cancel.is_casting = False

    async def _handle_cast_skill(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        sid   = payload.get("sid", "")
        tid   = int(payload.get("tid", -1))
        dir_x = float(payload.get("dir_x", 0.0))
        dir_y = float(payload.get("dir_y", 0.0))
        rage  = int(payload.get("rage", 0))
        mana  = int(payload.get("mana", 0))
        if sid:
            # Sincroniza rage/mana do cliente no ECS do servidor antes de processar a skill
            self.world_server.sync_player_resources(session.session_id, rage, mana)
            self.world_server.queue_skill(session.session_id, sid, tid, dir_x, dir_y, ts,
                                          dbg_seq=payload.get("dbg_seq", 0))

    async def _handle_player_stat_sync(self, session: Session, payload: dict, ts: int) -> None:
        """Recebe stats efetivos do cliente (equip/buff/consumível) e aplica ao servidor."""
        if not session.authenticated:
            return
        self.world_server.sync_player_combat_stats(session.session_id, payload)

    async def _handle_sell_request(self, session: Session, payload: dict, ts: int) -> None:
        """Processa venda ao mercador — gold ajustado server-side."""
        if not session.authenticated:
            return
        item_name    = str(payload.get("item_name", ""))
        item_value   = int(payload.get("item_value", 0))
        stack_sold   = max(1, int(payload.get("stack_sold", 1)))
        current_gold = payload.get("current_gold")
        result = self.world_server.process_shop_sell(
            session.session_id, item_name, item_value, stack_sold,
            current_gold=int(current_gold) if current_gold is not None else None)
        await session.send(MsgType.SELL_RESULT, result)

    async def _handle_buy_request(self, session: Session, payload: dict, ts: int) -> None:
        """Processa compra em loja — valida e aplica server-side.

        Gold é server-autoritativo: deduzido aqui, nunca confiado no SAVE_STATE.
        Inventário: cliente mantém localmente, servidor confirma espaço pelo último
        last_client_payload para evitar exploits de inventário infinito.
        """
        if not session.authenticated:
            return
        shop_id   = str(payload.get("shop_id", ""))
        item_name = str(payload.get("item_name", ""))
        quantity  = max(1, int(payload.get("quantity", 1)))
        last_inv     = session.last_client_payload.get("inventory") if session.last_client_payload else None
        current_gold = payload.get("current_gold")
        result = self.world_server.process_shop_buy(
            session.session_id, shop_id, item_name, quantity, last_inv,
            current_gold=int(current_gold) if current_gold is not None else None)
        await session.send(MsgType.BUY_RESULT, result)

    async def _handle_consumable_use(self, session: Session, payload: dict, ts: int) -> None:
        """Processa uso de consumível — aplica efeitos autoritativamente no servidor.

        Payload: {item_name, heal_instant, hot:{heal_per_tick,interval,ticks},
                  ooc_only, buffs:[]}
        Extensível: novos efeitos adicionados em 'buffs' sem mudar o handler.
        """
        if not session.authenticated:
            return
        self.world_server.apply_consumable(session.session_id, payload)

    async def _handle_save_state(self, session: Session, payload: dict, ts: int) -> None:
        """Recebe estado completo do cliente e persiste no banco."""
        if not session.authenticated or not session.char_data.get("id"):
            return
        from server.auth import save_character
        session.last_client_payload = payload   # cache para o save no disconnect
        # Inventário foi salvo — zera contador de compras pendentes
        self.world_server.confirm_inventory_save(session.session_id)
        # Sincroniza Wallet do servidor com o gold do cliente (pode ter loot coins não rastreados)
        _cli_gold_ss = payload.get("stats", {}).get("gold")
        if _cli_gold_ss is not None:
            from components import Wallet as _W_ss
            _eid_ss = self.world_server._player_eids.get(session.session_id)
            if _eid_ss is not None:
                _wall_ss = self.world_server.world.get_component(_eid_ss, _W_ss)
                if _wall_ss:
                    _wall_ss.gold = max(_wall_ss.gold, int(_cli_gold_ss))
        srv_data = self.world_server.get_player_save_data(session.session_id)
        merged   = self._build_save_merge(srv_data, payload)

        _eid_sv = self.world_server._player_eids.get(session.session_id)

        # Wallet.gold NÃO é sobrescrito aqui — é server-autoritativo via process_shop_buy/sell
        # e request_loot. get_player_save_data já lê wall.gold; _build_save_merge usa esse valor.

        # 2. Re-aplica talentos (reseta base_stamina → max_hp cai temporariamente)
        _client_tal = payload.get("talents", {})
        _tal_alloc  = _client_tal.get("allocated", {}) if isinstance(_client_tal, dict) else {}
        if _tal_alloc:
            try:
                self.world_server.apply_talent_effects_to_player(
                    session.session_id, _tal_alloc)
            except Exception as _te:
                print(f"[Session] aviso: talent effects não re-aplicados — {_te}")

        # 3. Re-aplica stat overrides (restaura max_hp ao valor com equipamento)
        #    DEVE vir antes do sync de HP — _apply_stat_overrides chama
        #    _recalculate_effective_stats que clamparia current_hp ao max_hp errado.
        if _eid_sv is not None and _eid_sv != -1:
            self.world_server._apply_stat_overrides(_eid_sv)

        # 4. Sincroniza HP do cliente — feito POR ÚLTIMO, após max_hp estar correto.
        #    Se feito antes, apply_char_stats_to_combat (passo 2) clamparia current_hp
        #    ao base_stamina (340) mesmo que o player estivesse com HP cheio (380).
        _cli_hp     = payload.get("stats", {}).get("current_hp")
        _cli_max_hp = payload.get("stats", {}).get("max_hp")
        if _eid_sv is not None and _cli_hp is not None:
            from components import CombatStats as _CSSv, CombatState as _CStSv
            _cs_sv  = self.world_server.world.get_component(_eid_sv, _CSSv)
            _cst_sv = self.world_server.world.get_component(_eid_sv, _CStSv)
            if _cs_sv:
                # max_hp: usa o maior entre servidor e cliente (servidor já tem bônus de talento)
                # Cap de 10 000 evita exploit de HP infinito via SAVE_STATE manipulado (NM2).
                _MAX_HP_CAP = 10_000
                if _cli_max_hp and int(_cli_max_hp) > _cs_sv.max_hp:
                    _cs_sv.max_hp = min(int(_cli_max_hp), _MAX_HP_CAP)
                # current_hp: cliente é fonte de verdade APENAS fora de combate.
                # Em combate, o servidor é autoritativo — ignorar valor do cliente evita
                # que SAVE_STATE (disparado por loot, inventário, etc.) resete o HP
                # acumulado por dano de mob, tornando o player efetivamente imortal.
                _in_combat = _cst_sv.in_combat if _cst_sv else False
                if not _in_combat:
                    _cs_sv.current_hp = max(1, min(_cs_sv.max_hp, int(_cli_hp)))
        try:
            await save_character(session.char_data["id"], merged)
        except Exception as e:
            print(f"[Session] ERRO save_state {session.username!r}: {e}")

    async def _handle_chat(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        text    = str(payload.get("text", ""))[:200]
        channel = payload.get("channel", "local")
        msg = {"sender": session.username, "text": text,
               "channel": channel, "color": [220, 210, 150]}
        if channel == "world":
            await self._broadcast_all(MsgType.CHAT_MESSAGE, msg)
        else:
            await self._broadcast_aoi_from_session(session, MsgType.CHAT_MESSAGE, msg)

    async def _handle_player_hp_sync(self, session: Session, payload: dict, ts: int) -> None:
        """Atualiza HP/maxHP do servidor após proc de item (servidor não tem dados de equip)."""
        if not session.authenticated:
            return
        hp     = int(payload.get("hp",     0))
        max_hp = int(payload.get("max_hp", 0))
        if hp <= 0 or max_hp <= 0:
            return
        from components import CombatStats as _CS_hp
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        cs = self.world_server.world.get_component(eid, _CS_hp)
        if cs:
            cs.max_hp     = max_hp
            cs.current_hp = min(hp, max_hp)

    async def _handle_gold_update(self, session: Session, payload: dict, ts: int) -> None:
        """Atualiza gold do servidor quando moedas são coletadas (loot, etc.)."""
        if not session.authenticated:
            return
        gold = int(payload.get("gold", 0))
        if gold < 0:
            return
        from components import Wallet as _W_gu
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        wall = self.world_server.world.get_component(eid, _W_gu)
        if wall:
            wall.gold = gold
        # Atualiza cache para o próximo save no disconnect
        if session.last_client_payload is not None:
            sess_stats = session.last_client_payload.get("stats")
            if isinstance(sess_stats, dict):
                sess_stats["gold"] = gold

    async def _handle_inventory_update(self, session: Session, payload: dict, ts: int) -> None:
        """Atualiza inventário do servidor quando item é coletado (loot, etc.)."""
        if not session.authenticated:
            return
        inventory = payload.get("inventory")
        if not isinstance(inventory, list):
            return
        if session.last_client_payload is None:
            session.last_client_payload = {}
        session.last_client_payload["inventory"] = inventory

    async def _handle_talent_update(self, session: Session, payload: dict, ts: int) -> None:
        """Salva talentos imediatamente quando um ponto é alocado/desalocado."""
        if not session.authenticated or not session.char_data.get("id"):
            return
        talents = payload.get("talents")
        if not isinstance(talents, dict):
            return
        # Atualiza cache e persiste só os talentos no DB
        if session.last_client_payload is None:
            session.last_client_payload = {}
        session.last_client_payload["talents"] = talents
        from server.auth import save_character
        srv_data = self.world_server.get_player_save_data(session.session_id)
        merged   = self._build_save_merge(srv_data, session.last_client_payload)
        try:
            await save_character(session.char_data["id"], merged)
        except Exception as e:
            print(f"[TalentUpdate] ERRO ao salvar: {e}")

    async def _handle_hotbar_update(self, session: Session, payload: dict, ts: int) -> None:
        """Atualiza cache da barra de ações — persistido no próximo save completo."""
        if not session.authenticated:
            return
        if session.last_client_payload is None:
            session.last_client_payload = {}
        # Atualiza skills (hotbar) no cache da sessão
        skills = payload.get("skills")
        if skills is not None:
            existing = session.last_client_payload.get("skills") or {}
            if isinstance(existing, dict):
                existing["hotbar"] = skills
            session.last_client_payload["skills"] = existing
        # Consumíveis são UI local — só precisam estar no próximo SAVE_STATE

    async def _handle_loot_request(self, session: Session, payload: dict, ts: int) -> None:
        """
        Player clicou num corpo para sacar.
        Se for o dono → retorna itens via LOOT_RESULT.
        Se não for o dono → ignora silenciosamente (regra de negócio).
        """
        if not session.authenticated:
            return
        corpse_id = int(payload.get("corpse_id", -1))
        if corpse_id < 0:
            return
        loot = self.world_server.request_loot(session.session_id, corpse_id)
        if loot is not None:
            await session.send(MsgType.LOOT_RESULT, {
                "corpse_id": corpse_id,
                "items":     loot["items"],
                "coins":     loot["coins"],
            })
            # Broadcast: corpo some para todos no AOI
            corpse_data = self.world_server._corpses.get(corpse_id, {})
            ctX = corpse_data.get("tx", 0)
            ctY = corpse_data.get("ty", 0)
            despawn_payload = {"eid": -corpse_id}
            for s in list(self._sessions.values()):
                if not s.authenticated:
                    continue
                sx, sy = self.world_server.get_tile_pos(s.session_id)
                if (sx - ctX) ** 2 + (sy - ctY) ** 2 <= AOI_RADIUS ** 2:
                    await s.send(MsgType.ENTITY_DESPAWN, despawn_payload)
                    s.known_eids.discard(-corpse_id)

    _handlers = {
        MsgType.LOGIN:        _handle_login,
        MsgType.LOGOUT:       _handle_logout,
        MsgType.MOVE:         _handle_move,
        MsgType.PING:         _handle_ping,
        MsgType.AUTO_ATTACK:  _handle_auto_attack,
        MsgType.CAST_SKILL:   _handle_cast_skill,
        MsgType.CANCEL_CAST:       _handle_cancel_cast,
        MsgType.PROJECTILE_HIT_CS: _handle_projectile_hit,
        MsgType.CHAT_SEND:         _handle_chat,
        MsgType.LOOT_REQUEST:      _handle_loot_request,
        MsgType.SAVE_STATE:        _handle_save_state,
        MsgType.PLAYER_STAT_SYNC:  _handle_player_stat_sync,
        MsgType.CONSUMABLE_USE:    _handle_consumable_use,
        MsgType.BUY_REQUEST:     _handle_buy_request,
        MsgType.SELL_REQUEST:      _handle_sell_request,
        MsgType.PLAYER_HP_SYNC:    _handle_player_hp_sync,
        MsgType.GOLD_UPDATE:       _handle_gold_update,
        MsgType.INV_SYNC:  _handle_inventory_update,
        MsgType.TALENT_UPDATE:     _handle_talent_update,
        MsgType.HOTBAR_UPDATE:     _handle_hotbar_update,
    }

    # ── AOI subscription — núcleo do sistema ─────────────────────────────────

    def _on_tick(self, tick_count: int, deltas: dict) -> None:
        if tick_count % (TICK_RATE * 300) == 0 and tick_count > 0:  # autosave a cada 5 min
            asyncio.create_task(self._autosave_all())
        # Dispatch sempre que há conteúdo — inclui skill_results que não entram em deltas
        has_pending = (any(deltas.values())
                       or bool(self.world_server._skill_results_this_tick)
                       or bool(self.world_server._pending_loot_notifications)
                       or bool(self.world_server._pending_xp_deliveries)
                       or bool(self.world_server._expired_corpses_this_tick)
                       or bool(self.world_server._player_hp_broadcasts_this_tick)
                       or bool(self.world_server._pending_sound_events))
        if not has_pending:
            return
        asyncio.create_task(self._dispatch_tick_deltas(deltas))

    async def _dispatch_tick_deltas(self, deltas: dict) -> None:
        """Distribui deltas para cada cliente respeitando known_eids (AOI subscription)."""
        try:
            # Mortes de players vão direto ao cliente morto (não AOI)
            await self._send_player_deaths(deltas)

            # Resultados de skills ANTES do AOI_UPDATE
            for skill_result in self.world_server.consume_skill_results():
                caster_eid = skill_result["caster_eid"]
                caster_sid = self.world_server.get_session_id_for_player(caster_eid)
                if not caster_sid:
                    continue
                cx, cy = self.world_server.get_tile_pos(caster_sid)
                for s in list(self._sessions.values()):
                    if not s.authenticated:
                        continue
                    sx, sy = self.world_server.get_tile_pos(s.session_id)
                    if (sx - cx) ** 2 + (sy - cy) ** 2 <= AOI_RADIUS ** 2:
                        await s.send(MsgType.SKILL_RESULT, skill_result)

            # Pré-calcula posições de todos os mobs UMA VEZ por tick.
            # Elimina O(mobs) component lookups por player por tick no sweep de AOI.
            # SpatialHash reduz o sweep de O(P×M) para O(P × candidatos_no_AOI).
            from components import TileMovement as _TM_aoi
            _mob_positions: dict[int, tuple] = {}
            _mob_hash = _SpatialHash(cell_size=AOI_RADIUS + 1)
            for _me in self.world_server._mob_eids:
                _mt = self.world_server.world.get_component(_me, _TM_aoi)
                if _mt:
                    _mob_positions[_me] = (_mt.current_tile_x, _mt.current_tile_y)
                    _mob_hash.insert(_me, _mt.current_tile_x, _mt.current_tile_y)

            for session in list(self._sessions.values()):
                if not session.authenticated:
                    continue
                tx, ty = self.world_server.get_tile_pos(session.session_id)
                update = self._build_update_for_session(session, deltas, tx, ty,
                                                        _mob_positions, _mob_hash)
                if update:
                    await session.send(MsgType.AOI_UPDATE, update)

            # Entrega XP proporcional aos jogadores
            xp_deliveries = self.world_server.consume_xp_deliveries()
            if xp_deliveries:
                for xp_entry in xp_deliveries:
                    sid = self.world_server.get_session_id_for_player(xp_entry["player_eid"])
                    if sid:
                        session = self._sessions.get(sid)
                        if session and session.authenticated:
                            _payload = {
                                "eid":       xp_entry["player_eid"],
                                "xp_gained": xp_entry["xp"],
                                "mob_eid":   xp_entry["mob_eid"],
                            }
                            # Inclui rage/mana se presentes (sync após skill consumir recursos)
                            if "rage" in xp_entry:
                                _payload["rage"] = xp_entry["rage"]
                            if "mana" in xp_entry:
                                _payload["mana"] = xp_entry["mana"]
                            if xp_entry.get("on_kill_skill"):
                                _payload["on_kill_skill"] = xp_entry["on_kill_skill"]
                            # Inclui hp sempre que presente (level-up ou skill de cura)
                            if "hp" in xp_entry:
                                _payload["hp"]     = xp_entry["hp"]
                                _payload["hp_max"] = xp_entry["hp_max"]
                            if "heal_amount" in xp_entry:
                                _payload["heal_amount"] = xp_entry["heal_amount"]
                                _payload["heal_sid"]    = xp_entry.get("heal_sid", "")
                            # Pontos de talento do servidor (level-up)
                            if "talent_points" in xp_entry:
                                _payload["talent_points"] = xp_entry["talent_points"]
                            # PvP: efeitos aplicados à vítima (disoriented, polymorph, root…)
                            if "applied_effects" in xp_entry:
                                _payload["applied_effects"]  = xp_entry["applied_effects"]
                                _payload["effect_durations"] = xp_entry.get("effect_durations", {})
                            # PvP: projétil chegando (BdF) — vítima cria visual cosmético
                            if "proj_incoming" in xp_entry:
                                _payload["proj_incoming"] = xp_entry["proj_incoming"]
                                _payload["proj_caster"]   = xp_entry.get("proj_caster", -1)
                                _payload["proj_target"]   = xp_entry.get("proj_target", -1)
                            await session.send(MsgType.STATS_UPDATE, _payload)

            # Notificações de corpse/loot
            for notif in self.world_server.consume_loot_notifications():
                corpse_id  = notif["corpse_id"]
                owner_eid  = notif["owner_eid"]
                notif_tx   = notif["tx"]
                notif_ty   = notif["ty"]

                # 1. ENTITY_SPAWN do corpo para todos no AOI (todos veem o corpo visualmente)
                spawn_payload = {
                    "eid":  -corpse_id,   # eid negativo = corpse (não conflita com mobs/players)
                    "kind": "corpse",
                    "tx":   notif_tx,
                    "ty":   notif_ty,
                }
                for s in list(self._sessions.values()):
                    if not s.authenticated:
                        continue
                    sx, sy = self.world_server.get_tile_pos(s.session_id)
                    if (sx - notif_tx) ** 2 + (sy - notif_ty) ** 2 <= AOI_RADIUS ** 2:
                        await s.send(MsgType.ENTITY_SPAWN, spawn_payload)
                        s.known_eids.add(-corpse_id)

                # 2. LOOT_AVAILABLE apenas ao dono (inclui lista de itens)
                owner_sid = self.world_server.get_session_id_for_player(owner_eid)
                if owner_sid:
                    owner_session = self._sessions.get(owner_sid)
                    if owner_session and owner_session.authenticated:
                        await owner_session.send(MsgType.LOOT_AVAILABLE, {
                            "corpse_id": corpse_id,
                            "tx":        notif_tx,
                            "ty":        notif_ty,
                            "items":     notif["items"],
                            "coins":     notif.get("coins", 0),
                        })

            # HP broadcasts: player se curou com skill — outros players no AOI atualizam barra
            _hp_bcast = self.world_server.consume_player_hp_broadcasts()
            if _hp_bcast:
                for _hp_upd in _hp_bcast:
                    _caster_eid = _hp_upd["eid"]
                    _caster_sid = self.world_server.get_session_id_for_player(_caster_eid)
                    _cx, _cy    = self.world_server.get_tile_pos(_caster_sid) if _caster_sid else (0, 0)
                    _hp_payload = {"eid": _caster_eid, "hp": _hp_upd["hp"], "hp_max": _hp_upd["hp_max"]}
                    for s in list(self._sessions.values()):
                        if not s.authenticated or s.session_id == _caster_sid:
                            continue  # não envia para o próprio caster (já tem via STATS_UPDATE)
                        sx, sy = self.world_server.get_tile_pos(s.session_id)
                        if (sx - _cx) ** 2 + (sy - _cy) ** 2 <= AOI_RADIUS ** 2:
                            await s.send(MsgType.STATS_UPDATE, _hp_payload)

            # Corpses que expiraram — notifica todos no AOI para remover visualmente
            for expired in self.world_server.consume_expired_corpses():
                cid = expired["cid"]
                ex, ey = expired["tx"], expired["ty"]
                despawn_payload = {"eid": -cid}
                for s in list(self._sessions.values()):
                    if not s.authenticated:
                        continue
                    sx, sy = self.world_server.get_tile_pos(s.session_id)
                    if (sx - ex) ** 2 + (sy - ey) ** 2 <= AOI_RADIUS ** 2:
                        await s.send(MsgType.ENTITY_DESPAWN, despawn_payload)
                        s.known_eids.discard(-cid)

            # Eventos de som posicionais (aggro de mob, etc.) → broadcast AOI
            for _snd_ev in self.world_server.consume_sound_events():
                _ev_tx = _snd_ev.get("tx", 0)
                _ev_ty = _snd_ev.get("ty", 0)
                for s in list(self._sessions.values()):
                    if not s.authenticated:
                        continue
                    sx, sy = self.world_server.get_tile_pos(s.session_id)
                    if (sx - _ev_tx) ** 2 + (sy - _ev_ty) ** 2 <= AOI_RADIUS ** 2:
                        await s.send(MsgType.SOUND_EVENT, _snd_ev)

            # Skill results já enviados no início (antes do AOI_UPDATE)
            # para garantir que o dano aparece antes do ENTITY_DESPAWN remover o mob

            # Correções de posição por skill (ex: Interceptar) — ENTITY_MOVE direto ao caster
            # O AOI_UPDATE ignora o próprio player (client-side prediction),
            # então o caster precisa de ENTITY_MOVE direto para aplicar o dash
            _pos_corrections = self.world_server.consume_skill_position_corrections()
            for pos_corr in _pos_corrections:
                p_sid = self.world_server.get_session_id_for_player(pos_corr["player_eid"])
                if p_sid:
                    p_session = self._sessions.get(p_sid)
                    if p_session and p_session.authenticated:
                        _move_payload = {
                            "eid": pos_corr["player_eid"],
                            "tx":  pos_corr["tx"],
                            "ty":  pos_corr["ty"],
                            "from_tx": pos_corr["tx"],
                            "from_ty": pos_corr["ty"],
                        }
                        if pos_corr.get("rejected"):
                            _move_payload["skill_rejected"] = True
                        await p_session.send(MsgType.ENTITY_MOVE, _move_payload)

        except Exception as e:
            import traceback
            print(f"[Session] ERRO em _dispatch_tick_deltas: {e}")
            traceback.print_exc()

    def _build_update_for_session(self, session: Session,
                                   deltas: dict, cx: int, cy: int,
                                   mob_positions: dict | None = None,
                                   mob_hash: "_SpatialHash | None" = None) -> dict:
        """
        Constrói AOI_UPDATE para uma sessão específica, com subscription tracking:
        - Entidade entra no AOI → ENTITY_SPAWN + adiciona a known_eids
        - Entidade sai do AOI  → ENTITY_DESPAWN + remove de known_eids
        - Entidade em AOI conhecida → ENTITY_MOVE
        """
        r = AOI_RADIUS
        result: dict = {}

        def in_aoi(tx: int, ty: int) -> bool:
            return (tx - cx) ** 2 + (ty - cy) ** 2 <= r * r

        # ── Moves: verifica entradas/saídas de AOI ────────────────────
        confirmed_moves = []
        aoi_exits       = []
        aoi_entries     = []

        for m in deltas.get("moved", []):
            eid    = m["eid"]
            in_new = in_aoi(m["tx"],      m["ty"])
            in_old = in_aoi(m["from_tx"], m["from_ty"])

            if eid in session.known_eids:
                if in_new:
                    confirmed_moves.append(m)   # ainda no AOI, envia move
                else:
                    aoi_exits.append(eid)       # saiu do AOI
                    session.known_eids.discard(eid)
            else:
                if in_new:
                    aoi_entries.append(eid)     # entrou no AOI pela primeira vez

        # ── Novas entidades no AOI (via move) ─────────────────────────
        for eid in aoi_entries:
            spawn_data = self.world_server.get_entity_spawn_data(eid)
            if spawn_data:
                result.setdefault("spawned", []).append(spawn_data)
                session.known_eids.add(eid)

        # ── Spawns novos (entidades criadas neste tick) ───────────────
        for sp in deltas.get("spawned", []):
            # Projéteis de mob: sempre enviar ao dono do alvo, sem AOI check.
            # São transientes — não entram em known_eids (sem despawn assimétrico).
            if sp.get("kind") == "mob_projectile":
                if sp.get("target_seid") == session.entity_id:
                    result.setdefault("spawned", []).append(sp)
                continue
            if in_aoi(sp["tx"], sp["ty"]):
                result.setdefault("spawned", []).append(sp)
                session.known_eids.add(sp["eid"])

        # ── Despawns ──────────────────────────────────────────────────
        final_despawned  = list(aoi_exits)
        _death_positions = deltas.get("despawned_pos", {})  # eid→(tx,ty)
        for eid in deltas.get("despawned", []):
            if eid in session.known_eids:
                final_despawned.append(eid)
                session.known_eids.discard(eid)
            else:
                # Mob morreu dentro do AOI mas cliente ainda não sabia dele
                # (ex: mob entrou/foi teleportado para perto do player e morto no mesmo tick)
                death_pos = _death_positions.get(eid)
                if death_pos and in_aoi(death_pos[0], death_pos[1]):
                    final_despawned.append(eid)

        # ── Combat: filtra eventos relevantes para o AOI desta sessão ─
        combat_events = [
            cr for cr in deltas.get("combat", [])
            if cr.get("target") in session.known_eids
            or cr.get("attacker") in session.known_eids
        ]

        # ── Effects: próprio player ───────────────────────────────────
        my_effects = [
            e for e in deltas.get("effects", [])
            if e.get("eid") == session.entity_id
        ]

        # ── Mob effects: mobs no AOI + outros players no AOI (PvP) ───
        # Inclui efeitos de outros players no mob_effects para que o
        # cliente possa exibi-los (enraged, root, etc.) nos players remotos.
        _raw_mob_efx = dict(
            (k, v) for k, v in deltas.get("mob_effects", {}).items()
            if int(k) in session.known_eids
        )
        for _pfx_entry in deltas.get("effects", []):
            _pfx_eid = _pfx_entry.get("eid")
            if (_pfx_eid is not None
                    and _pfx_eid != session.entity_id
                    and _pfx_eid in session.known_eids):
                _raw_mob_efx[str(_pfx_eid)] = _pfx_entry.get("effects", [])
        mob_effects = _raw_mob_efx

        # ── Monta resultado ───────────────────────────────────────────
        if confirmed_moves:  result["moved"]     = confirmed_moves
        if final_despawned:  result["despawned"] = list(set(final_despawned))
        if combat_events:    result["combat"]    = combat_events
        if my_effects:       result["effects"]   = my_effects
        if mob_effects:      result["mob_effects"] = mob_effects
        if deltas.get("stats"):
            result["stats"] = deltas["stats"]

        # Sweep: entidades em AOI não conhecidas (não detectadas via movimento).
        # Cobre mobs estacionários e players que entraram em range sem se mover.
        # SpatialHash filtra candidatos para O(mobs_no_AOI) por sessão em vez de O(M).
        if mob_hash is not None:
            _candidates = mob_hash.nearby(cx, cy, r)
        else:
            _candidates = mob_positions.keys() if mob_positions else ()
        for mob_eid in _candidates:
            if mob_eid in session.known_eids:
                continue
            pos = mob_positions.get(mob_eid) if mob_positions else None
            if pos and in_aoi(pos[0], pos[1]):
                spawn_data = self.world_server.get_entity_spawn_data(mob_eid)
                if spawn_data:
                    result.setdefault("spawned", []).append(spawn_data)
                    session.known_eids.add(mob_eid)

        for other_session in self._sessions.values():
            if not other_session.authenticated:
                continue
            other_eid = other_session.entity_id
            if other_eid == session.entity_id or other_eid in session.known_eids:
                continue
            ox, oy = self.world_server.get_tile_pos(other_session.session_id)
            if in_aoi(ox, oy):
                from components import TileMovement as _TM2
                from shared.constants import TILE_SIZE as _TS
                _hp, _hp_max = self.world_server.get_player_hp(other_session.session_id)
                spawn_payload = {
                    "eid":      other_eid,
                    "kind":     "player",
                    "tx":       ox, "ty": oy,
                    "name":     other_session.username,
                    "class_id": other_session.char_data.get("class_id", "guerreiro"),
                    "hp":       _hp,
                    "hp_max":   _hp_max,
                    "level":    other_session.char_data.get("level", 1),
                    "effects":  [],
                }
                result.setdefault("spawned", []).append(spawn_payload)
                session.known_eids.add(other_eid)

        return result

    # ── Player deaths — enviados diretamente, não via AOI_UPDATE ─────────────

    async def _autosave_all(self) -> None:
        from server.auth import save_character
        saved = 0
        for session in list(self._sessions.values()):
            if not session.authenticated or not session.char_data.get("id"):
                continue
            srv_data = self.world_server.get_player_save_data(session.session_id)
            if not srv_data:
                continue
            merged = self._build_save_merge(srv_data, session.last_client_payload)
            try:
                await save_character(session.char_data["id"], merged)
                saved += 1
            except Exception as e:
                print(f"[Session] ERRO autosave {session.username!r}: {e}")
        if saved:
            print(f"[Session] autosave  players={saved}  tick={self.world_server.tick_count}")

    async def _send_player_deaths(self, deltas: dict) -> None:
        for death in deltas.get("player_deaths", []):
            sid = death.get("session_id")
            if not sid:
                continue
            session = self._sessions.get(sid)
            if session:
                _death_peid = death["player_eid"]
                from components import CharacterStats as _CHS_death, CombatStats as _CS_death
                _char_d = self.world_server.world.get_component(_death_peid, _CHS_death)
                _cs_d   = self.world_server.world.get_component(_death_peid, _CS_death)
                await session.send(MsgType.PLAYER_DEATH, {
                    "eid":      _death_peid,
                    "mana":     _char_d.mana     if _char_d else 0,
                    "max_mana": _char_d.max_mana if _char_d else 0,
                    "hp":       _cs_d.current_hp if _cs_d   else 0,
                    "hp_max":   _cs_d.max_hp     if _cs_d   else 0,
                })

    # ── Broadcast helpers ─────────────────────────────────────────────────────

    async def _broadcast_all(self, msg_type: MsgType, payload: dict) -> None:
        tasks = [s.send(msg_type, payload)
                 for s in self._sessions.values() if s.authenticated]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_aoi_from_session(self, origin: Session,
                                          msg_type: MsgType, payload: dict) -> None:
        ox, oy = self.world_server.get_tile_pos(origin.session_id)
        tasks = []
        for s in self._sessions.values():
            if not s.authenticated:
                continue
            sx, sy = self.world_server.get_tile_pos(s.session_id)
            if (sx - ox) ** 2 + (sy - oy) ** 2 <= AOI_RADIUS ** 2:
                tasks.append(s.send(msg_type, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_aoi_except(self, origin: Session,
                                    msg_type: MsgType, payload: dict) -> None:
        ox, oy = self.world_server.get_tile_pos(origin.session_id)
        tasks = []
        for s in self._sessions.values():
            if not s.authenticated or s.session_id == origin.session_id:
                continue
            sx, sy = self.world_server.get_tile_pos(s.session_id)
            if (sx - ox) ** 2 + (sy - oy) ** 2 <= AOI_RADIUS ** 2:
                tasks.append(s.send(msg_type, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
