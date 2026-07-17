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
from server.log import log
import asyncio
import time

from shared.messages import MsgType, encode, decode
from shared.constants import AOI_RADIUS, AOI_EXIT_BUFFER, PROTOCOL_VERSION, TICK_RATE
from engine.utils import in_aoi as _in_aoi, SpatialHash as _SpatialHash


def _can_see(world, viewer_eid: int, target_eid: int) -> bool:
    """Regra centralizada de visibilidade servidor.

    - Sem CombatState → visível a todos.
    - is_visible=True  → visível a todos.
    - is_visible=False + viewer ghost + target ghost → visível (espírito vê espírito).
    - is_visible=False qualquer outro caso → invisível.
    """
    from engine.components import CombatState, GhostState
    cst = world.get_component(target_eid, CombatState)
    if cst is None or cst.is_visible:
        return True
    viewer_gst = world.get_component(viewer_eid, GhostState)
    if viewer_gst is None or not viewer_gst.is_ghost:
        return False
    target_gst = world.get_component(target_eid, GhostState)
    return target_gst is not None and target_gst.is_ghost


class Session:
    def __init__(self, ws, session_id: str):
        self.ws            = ws
        self.session_id    = session_id
        self.entity_id     = -1
        self.username      = ""
        self.char_data     = {}
        self.authenticated = False
        self.account_id    = -1    # preenchido no login; usado para criar personagem
        self._seq          = 0
        self.known_eids: set[int] = set()   # entidades que este cliente conhece
        # Último payload SAVE_STATE recebido do cliente (inventory/equipment/talents/skills/gold)
        self.last_client_payload: dict = {}
        # Serializa ws.send() nesta sessão: o handler de mensagens recebidas
        # (task própria por conexão, em server/main.py) e o loop de ticks
        # (task separada, via SessionManager._on_tick) podem chamar send()
        # concorrentemente na MESMA conexão — a lib `websockets` não é segura
        # pra send() concorrente (pode levantar ConcurrencyError). Sem lock,
        # essa falha era engolida em silêncio (ver abaixo) e o pacote se
        # perdia — causa real de "player remoto para de atualizar posição":
        # um ENTITY_SPAWN perdido nunca é reenviado, e known_eids do servidor
        # já foi marcado como entregue, então todo MOVE seguinte pra aquele
        # eid é descartado no cliente (entidade local nunca existiu).
        self._send_lock = asyncio.Lock()

    @property
    def display_name(self) -> str:
        """Nome do PERSONAGEM pra mostrar a outros players (nameplate, chat,
        trade) — NUNCA `self.username` (login da conta). Bug real: várias
        mensagens (ENTITY_SPAWN/AOI_UPDATE de player, CHAT_MESSAGE,
        TRADE_INVITE/TRADE_OPEN) mandavam `session.username` como "nome" —
        só coincidia com o nome do personagem quando o jogador escolhia os
        dois iguais; pra qualquer conta onde divergem, o nameplate/chat/
        trade mostrava o LOGIN da conta pros outros players. Fonte única
        agora — `char_data["name"]` é setado no login/seleção de personagem
        (ver _handle_select_character/_handle_create_character), cai pro
        username só como fallback defensivo (não deveria faltar em uso normal)."""
        return self.char_data.get("name") or self.username

    async def send(self, msg_type: MsgType, payload: dict) -> bool:
        """Envia ao cliente. Retorna True se entregue, False se falhou.

        Falhas NUNCA derrubam o servidor (conexão pode já estar fechando),
        mas agora são logadas — antes eram descartadas em silêncio, o que
        escondia o desync descrito acima até virar um bug "fantasma" só
        reproduzível em rede real (nunca em localhost)."""
        try:
            async with self._send_lock:
                self._seq += 1
                await self.ws.send(encode(msg_type, payload, seq=self._seq))
            return True
        except Exception as e:
            log.info(f"[Session] send falhou (session={self.session_id} "
                  f"type={msg_type}): {e!r}")
            return False

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
        log.info(f"[Session] +connect {session_id}  total={len(self._sessions)}")
        return session

    @staticmethod
    def _build_save_merge(srv_data: dict, client_payload: dict,
                          live_equipment: dict | None = None) -> dict:
        """
        Constrói o dict merged para save_character.
        Regras de autoridade:
        - Posição (tile_x, tile_y), hp, mp: servidor autoritativo
        - gold: SERVIDOR autoritativo (Wallet.gold no ECS — atualizado por compras/vendas/loot/
          GOLD_UPDATE já validado, ver _handle_gold_update). NUNCA confiar no payload do cliente
          aqui — fazia isso antes (`_cli_gold if _cli_gold > 0 else _srv_gold`), permitindo
          persistir qualquer valor de gold mandado via SAVE_STATE (ver
          arquitetura/PROBLEMAS_ARQUITETURA.md, vulnerabilidade de gold absoluto).
        - max_hp: SERVIDOR autoritativo (CombatStats.max_hp já inclui bônus de equipamento
          validado, ver Tier B — _reconstruct_item). Mesma razão acima.
        - inventory, talents: cliente se disponível, None = não sobrescreve DB
        - equipment: SERVIDOR autoritativo quando `live_equipment` é fornecido — bug real
          (11/07/2026): aljava sempre voltava cheia no relogin porque `client_payload["equipment"]`
          é só um cache (`session.last_client_payload`) do último EQUIP_SYNC/SAVE_STATE que o
          cliente mandou, nunca atualizado quando flechas são gastas em combate normal (isso é
          100% server-side, `_server_apply_ranged_physical` mexe direto no Equipment do ECS sem
          avisar o cliente) — o cache ficava com a contagem de quando a aljava foi equipada
          (cheia) e essa versão stale sobrescrevia o DB no disconnect/autosave. `live_equipment`
          (WorldServer.get_player_equipment_data, chamado na hora do save) é o Equipment ATUAL
          do ECS — sempre correto, elimina a janela de staleness. client_p["equipment"] só
          sobra como fallback se o entity já não existir mais (ex: corrida rara no disconnect).
        - skills: cliente se disponível, fallback srv_data
        - quests: SEMPRE servidor (mesma regra de skill_levels — progresso/entrega de
          quest é server-autoritativo, ver quest_logic.py/PROBLEMAS_ARQUITETURA.md)
        - stats base (level, xp, attrs): servidor
        """
        client_p  = client_payload
        srv_stats = srv_data.get("stats", {})
        # gold e max_hp já vêm corretos de get_player_save_data (Wallet/CombatStats vivos) —
        # dict(srv_stats) preserva os dois sem precisar (e sem dever) olhar o payload do cliente.
        merged_stats = dict(srv_stats)
        # Usa client_skills apenas se tiver "learned" não-vazia; {"learned":[]} é falsy
        # para evitar que um save com lista vazia sobrescreva skills válidas no DB.
        _cs_raw = client_p.get("skills") if client_p else None
        client_skills = _cs_raw if (_cs_raw and _cs_raw.get("learned")) else None

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
            "equipment": live_equipment if live_equipment else (client_p.get("equipment") if client_p else None),
            "talents":   client_p.get("talents")   if client_p else None,
            "skills":    client_skills if client_skills else (srv_data.get("skills") or None),
            "fog":       merged_fog if merged_fog else None,
            # skill_levels: SEMPRE servidor — progressão por uso é
            # server-autoritativa, cliente nunca influencia (ver
            # stats_system.grant_skill_xp / PROBLEMAS_ARQUITETURA.md).
            "skill_levels": srv_data.get("skill_levels"),
            # quests: SEMPRE servidor — progresso/entrega é server-autoritativa,
            # mesma regra de skill_levels (ver quest_logic.py/PROBLEMAS_ARQUITETURA.md).
            "quests": srv_data.get("quests"),
            # map_id: SERVIDOR autoritativo — zona atual do player.
            "map_id": srv_data.get("map_id"),
        }

    async def on_disconnect(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        if session.entity_id != -1:
            # Se o player está morto/fantasma, revive no cemitério ANTES de
            # salvar — sem isso, o relogin trazia o personagem "vivo" na
            # posição crua salva (local da morte ou onde o fantasma vagou) e
            # deixava o marcador de corpo órfão pra quem já o via no AOI
            # (ver RespawnMixin._auto_revive_on_disconnect).
            self.world_server._auto_revive_on_disconnect(session.entity_id)
            # Salva ANTES de remover a entidade do ECS
            if session.authenticated and session.char_data.get("id"):
                from server.auth import save_character
                srv_data = self.world_server.get_player_save_data(session_id)
                if srv_data:
                    _live_eq = self.world_server.get_player_equipment_data(session_id)
                    merged = self._build_save_merge(srv_data, session.last_client_payload, _live_eq)
                    try:
                        await save_character(session.char_data["id"], merged)
                        log.info(f"[Session] saved {session.username!r}  "
                              f"tile=({merged['tile_x']},{merged['tile_y']})  "
                              f"hp={merged['hp']}  gold={merged['stats'].get('gold', 0)}")
                    except Exception as e:
                        log.error(f"[Session] ERRO ao salvar {session.username!r}: {e}")
            self._eid_to_sid.pop(session.entity_id, None)
            eid = session.entity_id
            # Cancela trade ativa (se houver) ANTES de despawnar — avisa o
            # outro lado, que senão ficaria com uma janela de trade aberta
            # apontando pra um player que já não existe mais no world.
            trade_session = self.world_server.get_trade_session(eid)
            if trade_session is not None:
                other_eid = trade_session.other_of(eid)
                trade_id = self.world_server.cancel_trade(eid, "disconnect")
                other_sid = self.world_server.get_session_id_for_player(other_eid)
                other_session = self._sessions.get(other_sid) if other_sid else None
                if other_session and other_session.authenticated:
                    await other_session.send(MsgType.TRADE_CANCELLED,
                                              {"trade_id": trade_id, "reason": "disconnect"})
            # Duelo ativo/convites pendentes morrem no logout — o DUEL_END
            # pro lado que ficou sai pelo broadcast loop do próximo tick
            # (consume_duel_end_events), o desconectado não precisa receber.
            self.world_server.end_duels_of(eid, "disconnect")
            # Grupo: mesma lógica — o PARTY_STATE pro resto do grupo sai
            # pelo broadcast loop do próximo tick (consume_party_state_events).
            self.world_server.end_parties_of(eid)
            for other in self._sessions.values():
                other.known_eids.discard(eid)
            await self._broadcast_all(MsgType.ENTITY_DESPAWN, {"eid": eid})
            self.world_server.despawn_player(session_id)
        log.info(f"[Session] -disconnect {session.username!r}")

    async def on_message(self, session: Session, raw: str) -> None:
        try:
            msg_type, payload, seq, ts = decode(raw)
        except ValueError as e:
            await session.send(MsgType.ERROR, {"reason": str(e)})
            return
        # Validação de payload na borda (item B4, PROBLEMAS_ARQUITETURA §11):
        # campos núcleo ausentes/de tipo errado nunca chegam ao handler —
        # ERROR de volta + log, em vez de bug silencioso via .get(default).
        from shared.messages import validate_c2s
        _verr = validate_c2s(msg_type, payload)
        if _verr is not None:
            log.warning(f"[Session] payload inválido de {session.username or session.session_id!r}: "
                        f"{msg_type.value} → {_verr}")
            await session.send(MsgType.ERROR, {"reason": f"invalid_payload:{_verr}"})
            return
        handler = self._handlers.get(msg_type)
        if handler:
            try:
                await handler(self, session, payload, ts)
            except Exception as e:
                import traceback
                log.error(f"[Session] ERRO em handler {msg_type}: {e}")
                traceback.print_exc()
                await session.send(MsgType.ERROR, {"reason": f"server_error:{type(e).__name__}"})

    # ── Handlers C→S ─────────────────────────────────────────────────────────

    async def _handle_register(self, session: Session, payload: dict, ts: int) -> None:
        """Cria conta (sem personagem). password já vem SHA-256 do cliente."""
        if session.authenticated:
            await session.send(MsgType.REGISTER_ERROR, {"reason": "already_logged_in"})
            return
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", "")).strip()
        if not username or not password or len(username) < 3 or len(username) > 20:
            await session.send(MsgType.REGISTER_ERROR, {"reason": "invalid_input"})
            return
        from server.auth import register as _auth_register
        ok = await _auth_register(username, password)
        if ok:
            await session.send(MsgType.REGISTER_OK, {})
        else:
            await session.send(MsgType.REGISTER_ERROR, {"reason": "username_taken"})

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

        # Armazena dados básicos (sem spawnar ainda — escolha de personagem vem depois)
        session.username      = username
        session.account_id    = char_data.get("account_id", -1)
        session.authenticated = True

        chars = char_data.get("characters", [])
        await session.send(MsgType.AUTH_OK, {
            "token":      session.session_id,
            "characters": chars,
            "server_ts":  int(time.time() * 1000),
        })
        log.info(f"[Session] auth ok: {username!r}  chars={len(chars)}")

    async def _handle_move(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        tx = int(payload.get("tx", 0))
        ty = int(payload.get("ty", 0))
        accepted = self.world_server.move_player(session.session_id, tx, ty)
        if accepted:
            # _broadcast_aoi_except (não _broadcast_aoi_from_session): o próprio
            # remetente NÃO deve receber de volta o próprio move confirmado — ele
            # já sabe que se moveu (foi ele quem mandou). Recebendo de volta, o
            # cliente trata isso como "correção do servidor" (eid==self._my_eid em
            # _handle_msg_entity_move) e faz snap instantâneo cancelando a animação
            # local em andamento — todo passo de caminhada normal virava um
            # teleporte, já que o eco quase sempre chega com a animação ainda em
            # progresso (current_tile_x != tx confirmado).
            await self._broadcast_aoi_except(session, MsgType.ENTITY_MOVE, {
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
            from engine.components import CombatState as _CS
            from engine.stat_fns import enter_combat as _ec
            player_eid = session.entity_id
            pcst = self.world_server.world.get_component(player_eid, _CS)
            if pcst:
                _ec(pcst)
                pcst.is_pursuing = True   # espelha o clique direito do cliente
        else:
            from engine.components import CombatState as _CS_stop
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
        # Cast estava mesmo pendente? (cancel que chega DEPOIS da conclusão — race —
        # não pode apagar o cooldown legítimo registrado)
        _was_pending = any(
            e["player_eid"] == player_eid and e["spell_id"] == sid
            for e in self.world_server._pending_spell_completions
        ) or any(
            e["player_eid"] == player_eid and e["sid"] == sid
            for e in self.world_server._pending_skill_requests
        )
        # Remove entradas pendentes (timer ainda correndo)
        self.world_server._pending_spell_completions = [
            e for e in self.world_server._pending_spell_completions
            if not (e["player_eid"] == player_eid and e["spell_id"] == sid)
        ]
        # Remove CAST_SKILL ainda não processado (fila do tick): cast iniciado e
        # cancelado por movimento no mesmo frame chega aqui ANTES do request ser
        # convertido em _pending_spell_completions no próximo tick — sem isso o
        # cast completava normalmente mesmo após o CANCEL_CAST.
        self.world_server._pending_skill_requests = [
            e for e in self.world_server._pending_skill_requests
            if not (e["player_eid"] == player_eid and e["sid"] == sid)
        ]
        # Cast genuinamente cancelado → desarma o CD registrado no início do cast
        # (skill_processor registra _skill_last_used quando o handler aceita o
        # cast; sem este pop, cancelar por movimento deixava o CD rodando só no
        # servidor e o próximo cast era rejeitado com o cliente sem CD na hotbar).
        if _was_pending:
            self.world_server._skill_last_used.pop((player_eid, sid), None)
        # Canção de Ninar cancelada no meio do canal: acorda os alvos que já
        # dormiram (sono é aplicado no início do canal) — igual ao offline
        # _cancel_cancao_ninar.
        if sid == "cancao_ninar":
            from engine.components import CharacterStats as _CSCancelLullaby, StatusEffects as _SFXCancelLullaby
            _char_stats = self.world_server.world.get_component(player_eid, _CSCancelLullaby)
            if _char_stats and _char_stats.lullaby_targets:
                for _tid in _char_stats.lullaby_targets:
                    _sfx = self.world_server.world.get_component(_tid, _SFXCancelLullaby)
                    if _sfx and _sfx.has("sleep"):
                        _eff = _sfx.get("sleep")
                        if _eff:
                            _eff.on_expire_effect = ""
                        _sfx.remove("sleep")
                _char_stats.lullaby_targets.clear()
        # Cast cancelado e nenhum outro cast pendente → libera is_casting
        # (e portanto o auto-attack via can_act()).
        if not any(e["player_eid"] == player_eid
                   for e in self.world_server._pending_spell_completions):
            from engine.components import CombatState as _CStCancelCast
            _cst_cancel_cast = self.world_server.world.get_component(player_eid, _CStCancelCast)
            if _cst_cancel_cast:
                _cst_cancel_cast.is_casting = False
        # Remove entradas já em voo (timer expirou, aguardando PROJECTILE_HIT_CS).
        # Sem isso, cast cancelado no último frame ainda causa dano quando o projétil
        # visual (criado por is_completion) chega ao alvo e envia PROJECTILE_HIT_CS.
        self.world_server._spells_in_flight_queue = [
            e for e in self.world_server._spells_in_flight_queue
            if not (e["player_eid"] == player_eid and e["spell_id"] == sid)
        ]
        # Channeling cancelado (ex: Calamidade Flamejante interrompida por movimento):
        # remove o componente Channeling do player para parar os ticks de dano no servidor.
        from engine.components import Channeling as _ChanCancel
        _ch_comp = self.world_server.world.get_component(player_eid, _ChanCancel)
        if _ch_comp and _ch_comp.spell_id == sid:
            try:
                self.world_server.world.remove_component(player_eid, _ChanCancel)
            except Exception:
                pass
            from engine.components import CombatState as _CStCancel
            _cst_cancel = self.world_server.world.get_component(player_eid, _CStCancel)
            if _cst_cancel:
                _cst_cancel.is_casting = False

    async def _handle_cast_dir_update(self, session: Session, payload: dict, ts: int) -> None:
        """Atualiza direção de skill direcional pendente (ex: tiro_multiplo).
        Enviado pelo cliente na conclusão do cast local, capturando a posição
        do mouse NAQUELE momento (não quando a tecla foi pressionada)."""
        if not session.authenticated:
            return
        sid   = payload.get("sid", "")
        dir_x = float(payload.get("dir_x", 0.0))
        dir_y = float(payload.get("dir_y", 0.0))
        if not sid or (dir_x == 0.0 and dir_y == 0.0):
            return
        player_eid = self.world_server.get_entity_id(session.session_id)
        for entry in self.world_server._pending_spell_completions:
            if entry["player_eid"] == player_eid and entry["spell_id"] == sid:
                entry["dir_x"] = dir_x
                entry["dir_y"] = dir_y
                break

    async def _handle_cast_skill(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        sid   = payload.get("sid", "")
        tid   = int(payload.get("tid", -1))
        dir_x = float(payload.get("dir_x", 0.0))
        dir_y = float(payload.get("dir_y", 0.0))
        # payload["rage"]/["mana"] NUNCA são lidos aqui — eram adotados direto via
        # sync_player_resources ("cliente é fonte de verdade"), o que sobrescrevia
        # o rage/mana REAL do servidor com o que o CLIENTE mandasse. Bug real
        # encontrado: CombatStats.mana do cliente (campo espelho, default 0) ainda
        # não tinha sido sincronizado com CharacterStats.mana (cheio) no momento do
        # primeiro cast após login — o cliente mandava mana=0 e zerava a mana real
        # do servidor (que já estava correta, calculada em spawn_player), fazendo
        # TODA skill de Mago falhar por "Mana insuficiente" logo após logar, até
        # algo no cliente acabar sincronizando o espelho corretamente. O servidor
        # já rastreia rage/mana 100% por conta própria (ganho de rage em
        # combat_processor.py, custo deduzido nos próprios handlers de skill +
        # _process_spell_cast_completions) — nunca precisou confiar no cliente pra
        # isso. Ver arquitetura/PROBLEMAS_ARQUITETURA.md.
        if sid:
            self.world_server.queue_skill(session.session_id, sid, tid, dir_x, dir_y, ts,
                                          dbg_seq=payload.get("dbg_seq", 0))

    async def _handle_player_stat_sync(self, session: Session, payload: dict, ts: int) -> None:
        """Obsoleto. Aplicava direto qualquer attack_power/crit_rating/armor/spell_power/
        etc. que o cliente mandasse, sem validar contra nada — um cliente malicioso virava
        deus permanentemente com um único float forjado (ver
        arquitetura/PROBLEMAS_ARQUITETURA.md). O servidor agora deriva esses stats ele
        mesmo a partir do Equipment/TalentTree reais (WorldServer._apply_equipment_modifiers
        / apply_talent_effects_to_player) — não há mais uso legítimo. Mantida só para
        clientes antigos não quebrarem ao enviá-la."""
        return

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
        # Sanitiza talentos no próprio payload ANTES de cachear/usar no merge —
        # tanto session.last_client_payload (usado em saves futuros, ex:
        # disconnect) quanto o merge desta chamada precisam da versão
        # validada, nunca da alocação crua que o cliente mandou (ver
        # WorldServer.validate_talent_allocation).
        _raw_tal = payload.get("talents")
        if isinstance(_raw_tal, dict):
            _checked_tal = self.world_server.validate_talent_allocation(session.session_id, _raw_tal)
            if _checked_tal:
                payload["talents"] = _checked_tal
            else:
                payload.pop("talents", None)
        # Sanitiza o inventário na MESMA borda (item A4, PROBLEMAS_ARQUITETURA
        # seção 11): cada item round-trip pelo catálogo autoritativo — o que
        # persiste é o item do catálogo + bookkeeping clampado, nunca o dict
        # cru do cliente. Item de nome desconhecido (forjado) é descartado.
        _san_inv = self.world_server.sanitize_inventory_payload(payload.get("inventory"))
        if _san_inv is not None:
            payload["inventory"] = _san_inv
        else:
            payload.pop("inventory", None)
        session.last_client_payload = payload   # cache para o save no disconnect
        # Inventário foi salvo — zera contador de compras pendentes
        self.world_server.confirm_inventory_save(session.session_id)
        srv_data = self.world_server.get_player_save_data(session.session_id)
        _live_eq = self.world_server.get_player_equipment_data(session.session_id)
        merged   = self._build_save_merge(srv_data, payload, _live_eq)

        # Wallet.gold NÃO é sobrescrito aqui — é server-autoritativo via process_shop_buy/sell,
        # request_loot e GOLD_UPDATE (já validado/limitado, ver _handle_gold_update). SAVE_STATE
        # NUNCA deve adotar o gold reportado pelo cliente — fazia isso aqui antes
        # (`_wall_ss.gold = max(_wall_ss.gold, int(_cli_gold_ss))`), permitindo que qualquer
        # SAVE_STATE com stats.gold inflado virasse gold real e persistente (ver
        # arquitetura/PROBLEMAS_ARQUITETURA.md). get_player_save_data já lê wall.gold puro;
        # _build_save_merge usa esse valor sem olhar o payload.

        # Re-aplica talentos (cs_flags + modifiers de atributo) — payload["talents"]
        # já foi validado/sanitizado no topo desta função, usa direto sem revalidar
        # (já reflete o orçamento real do servidor).
        _checked_tal_alloc = payload.get("talents", {}).get("allocated", {})
        if _checked_tal_alloc:
            try:
                self.world_server.apply_talent_effects_to_player(
                    session.session_id, _checked_tal_alloc)
            except Exception as _te:
                log.warning(f"[Session] aviso: talent effects não re-aplicados — {_te}")

        # Skills: atualiza PlayerSkills.learned_skill_ids no ECS vivo do servidor
        # para que _process_quest_events::sync_learn_skill_progress detecte skills
        # recém-aprendidas via treinador e avance objetivos de quest learn_skill.
        # O aprendizado de skills é client-side (gap pré-existente — trainer economy
        # não é server-validada); aqui só sincronizamos o estado resultante para que
        # o servidor saiba quais skills o jogador tem agora.
        _skills_ss = payload.get("skills")
        if isinstance(_skills_ss, dict):
            self.world_server.sync_player_skills(session.session_id, _skills_ss)

        # max_hp/current_hp NUNCA são lidos do payload aqui. max_hp já é
        # server-autoritativo (CombatStats deriva de Equipment/TalentTree reais —
        # ver _apply_equipment_modifiers, Tier B/C) e current_hp idem (dano, regen,
        # cura de consumível e proc já são 100% server-side, ver Tier D). Antes
        # havia um bloco aqui que adotava `stats.max_hp`/`stats.current_hp` do
        # cliente (com cap de 10.000) — vestígio de quando equipamento não tinha
        # modelagem server-side; root cause removido junto da causa (ver
        # arquitetura/PROBLEMAS_ARQUITETURA.md).
        try:
            await save_character(session.char_data["id"], merged)
        except Exception as e:
            log.error(f"[Session] ERRO save_state {session.username!r}: {e}")

    async def _handle_chat(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        text    = str(payload.get("text", ""))[:200]
        channel = payload.get("channel", "local")
        msg = {"sender": session.display_name, "text": text,
               "channel": channel, "color": [220, 210, 150]}
        if channel == "world":
            await self._broadcast_all(MsgType.CHAT_MESSAGE, msg)
        else:
            await self._broadcast_aoi_from_session(session, MsgType.CHAT_MESSAGE, msg)

    async def _handle_player_hp_sync(self, session: Session, payload: dict, ts: int) -> None:
        """Obsoleto desde que procs de item passaram a ser rolados pelo próprio
        servidor (core_systems.ServerCombatStateSystem._roll_procs, Tier D — ver
        arquitetura/PROBLEMAS_ARQUITETURA.md). Antes disso esta mensagem era o
        ÚNICO jeito do servidor saber de mudanças de HP por proc, e o handler
        adotava hp/max_hp do cliente direto, sem nenhum teto — um cliente
        malicioso podia mandar PLAYER_HP_SYNC com hp/max_hp arbitrários a
        qualquer momento. Hoje o servidor já é autoritativo pra toda fonte de
        mudança de HP (dano, regen, cura de consumível, proc) — não há mais
        nenhum uso legítimo, então a mensagem é ignorada. Mantida só para
        clientes antigos não quebrarem ao enviá-la."""
        return

    async def _handle_equip_sync(self, session: Session, payload: dict, ts: int) -> None:
        """Atualiza o componente Equipment do servidor quando o player equipa/desequipa."""
        if not session.authenticated:
            return
        equipment = payload.get("equipment")
        if not isinstance(equipment, dict):
            return
        rejected = self.world_server.update_player_equipment(session.session_id, equipment)
        for _rej in rejected:
            await session.send(MsgType.EQUIP_REJECTED, _rej)
        # Persiste no cache de save o estado REAL pós-validação (não o payload
        # cru do cliente) — senão um slot rejeitado (classe/level) ainda seria
        # salvo no disconnect via _build_save_merge, mesmo nunca tendo sido
        # aplicado ao Equipment ao vivo do servidor.
        if session.last_client_payload is None:
            session.last_client_payload = {}
        session.last_client_payload["equipment"] = \
            self.world_server.get_player_equipment_data(session.session_id)

    # Maior recompensa de gold de missão conhecida hoje (quests_data.py) é 25 —
    # 500 é generoso pra cobrir conteúdo futuro sem permitir "setar" gold arbitrário.
    _GOLD_UPDATE_DELTA_CAP = 500
    _GOLD_HARD_CAP         = 1_000_000

    async def _handle_gold_update(self, session: Session, payload: dict, ts: int) -> None:
        """Aplica um INCREMENTO de gold reportado pelo cliente (hoje, só recompensa de
        missão — loot de moedas já é aplicado autoritativamente em loot_processor.py).

        NUNCA adota o valor absoluto do cliente — fazia isso antes (`wall.gold = gold`),
        deixando qualquer GOLD_UPDATE com `{"gold": 999999999}` setar o gold direto, sem
        teto algum (ver arquitetura/PROBLEMAS_ARQUITETURA.md, vulnerabilidade de gold
        absoluto). Em vez disso: calcula o delta contra o gold real do servidor, rejeita
        deltas negativos (perda de gold não passa por aqui) e limita o incremento a
        _GOLD_UPDATE_DELTA_CAP por mensagem + _GOLD_HARD_CAP no total.

        O sistema de missões ainda calcula a recompensa no cliente (quest_system.py não
        tem contraparte server-side) — isso é uma lacuna maior, registrada em
        PROBLEMAS_ARQUITETURA.md; este cap é mitigação, não a correção definitiva."""
        if not session.authenticated:
            return
        claimed = int(payload.get("gold", 0))
        if claimed < 0:
            return
        from engine.components import Wallet as _W_gu
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        wall = self.world_server.world.get_component(eid, _W_gu)
        if not wall:
            return
        delta = claimed - wall.gold
        if delta <= 0:
            return
        delta = min(delta, self._GOLD_UPDATE_DELTA_CAP)
        wall.gold = min(wall.gold + delta, self._GOLD_HARD_CAP)

    async def _handle_inventory_update(self, session: Session, payload: dict, ts: int) -> None:
        """Atualiza inventário do servidor quando item é coletado (loot, etc.)."""
        if not session.authenticated:
            return
        inventory = self.world_server.sanitize_inventory_payload(payload.get("inventory"))
        if inventory is None:
            return
        if session.last_client_payload is None:
            session.last_client_payload = {}
        # Cacheia a versão SANITIZADA (round-trip pelo catálogo, item A4 —
        # PROBLEMAS_ARQUITETURA seção 11), nunca o payload cru — este cache
        # alimenta _build_save_merge no save/disconnect.
        session.last_client_payload["inventory"] = inventory
        # Reconstrói a cópia local (Inventory ECS) para que handlers de skill
        # (Recarregar, Tiro Múltiplo, etc.) validem munição com dados reais —
        # sem isso o componente fica vazio e a validação sempre falha/é pulada.
        self.world_server.sync_player_inventory(session.session_id, inventory)

        # Sincroniza objetivos collect_item IMEDIATAMENTE após o Inventory
        # real do servidor mudar (loot é o gatilho mais comum de INV_SYNC).
        # Antes, o progresso só era recalculado como efeito colateral de
        # OUTROS eventos de quest (kill, use_skill) passando por
        # _process_quest_events — pegar um item de quest não disparava nada
        # por si só, e o contador só atualizava na PRÓXIMA vez que um evento
        # não relacionado rodasse (ex: o próximo kill) — daí o salto "0 → 2"
        # relatado por testers (dropou a 1ª presa: não contou; dropou a 2ª:
        # contou 2 de uma vez). Ver PROBLEMAS_ARQUITETURA.md.
        from engine.components import QuestLog as _QLinv, Inventory as _InvQuestSync
        import engine.quest_logic as _qlogic_inv
        _inv_eid = self.world_server.get_entity_id(session.session_id)
        _ql_inv  = self.world_server.world.get_component(_inv_eid, _QLinv) \
                   if _inv_eid != -1 else None
        if _ql_inv and _ql_inv.active:
            _inv_comp = self.world_server.world.get_component(_inv_eid, _InvQuestSync)
            if _qlogic_inv.sync_collect_progress(_ql_inv, _inv_comp):
                await session.send(MsgType.QUEST_UPDATE, {
                    "active":    {q: list(p) for q, p in _ql_inv.active.items()},
                    "completed": list(_ql_inv.completed),
                })

    async def _handle_talent_update(self, session: Session, payload: dict, ts: int) -> None:
        """Salva talentos imediatamente quando um ponto é alocado/desalocado."""
        if not session.authenticated or not session.char_data.get("id"):
            return
        talents = payload.get("talents")
        if not isinstance(talents, dict):
            return
        # Valida contra o orçamento real do TalentTree do servidor ANTES de
        # cachear/persistir — senão um payload forjado (ex: 999 pontos num
        # talento) ficava salvo no banco e voltava a valer no próximo login
        # (ver WorldServer.validate_talent_allocation).
        _checked = self.world_server.validate_talent_allocation(session.session_id, talents)
        if not _checked:
            return
        # Atualiza cache e persiste só os talentos no DB
        if session.last_client_payload is None:
            session.last_client_payload = {}
        session.last_client_payload["talents"] = _checked
        from server.auth import save_character
        srv_data = self.world_server.get_player_save_data(session.session_id)
        _live_eq = self.world_server.get_player_equipment_data(session.session_id)
        merged   = self._build_save_merge(srv_data, session.last_client_payload, _live_eq)
        try:
            await save_character(session.char_data["id"], merged)
        except Exception as e:
            log.error(f"[TalentUpdate] ERRO ao salvar: {e}")

    async def _handle_quest_accept(self, session: Session, payload: dict, ts: int) -> None:
        """Aceita quest a partir do diálogo de NPC — valida pré-requisitos/
        nível com o QuestLog/CharacterStats do PRÓPRIO servidor (nunca confia
        no cliente). Responde QUEST_UPDATE sempre (sucesso ou não — cliente
        só reflete o que o servidor confirma, nunca muta localmente).

        Aceita também quest_id="" (vazio) vindo de interações com NPCs que não
        são QuestGivers (ex: ShopSystem abriu loja de Fabian Hardek) — nesse
        caso só aplica o evento talk_to_npc sem tentar iniciar nenhuma quest."""
        if not session.authenticated or not session.char_data.get("id"):
            return
        qid = str(payload.get("quest_id", ""))
        eid = session.entity_id
        from engine.components import QuestLog
        ql = self.world_server.world.get_component(eid, QuestLog)
        if ql is None:
            return
        import engine.quest_logic as quest_logic
        # talk_to_npc SEMPRE antes do try_start — mesmo quando quest_id é vazio
        # (interação com NPC não-QuestGiver, ex: mercador). O check "if not qid"
        # antigo ficava ANTES disso e impedia o evento de chegar ao QuestLog.
        npc_name = payload.get("npc_name", "")
        if npc_name:
            quest_logic.apply_event(ql, "talk_to_npc", {"npc_name": npc_name})
        if not qid:
            # Só interação de NPC, sem quest pra aceitar — envia o estado atual
            await session.send(MsgType.QUEST_UPDATE, {
                "active":    {q: list(p) for q, p in ql.active.items()},
                "completed": list(ql.completed),
            })
            return
        quest_logic.try_start(self.world_server.world, eid, ql, qid)
        await session.send(MsgType.QUEST_UPDATE, {
            "active":    {q: list(p) for q, p in ql.active.items()},
            "completed": list(ql.completed),
        })

    async def _handle_quest_turn_in(self, session: Session, payload: dict, ts: int) -> None:
        """Entrega quest a partir do diálogo de NPC — valida objetivos
        completos contra o QuestLog do servidor, concede XP/gold e remove
        itens de quest do Inventory. Server-autoritativo — ver quest_logic.py
        e arquitetura/PROBLEMAS_ARQUITETURA.md (migração de quests)."""
        if not session.authenticated or not session.char_data.get("id"):
            return
        qid = str(payload.get("quest_id", ""))
        if not qid:
            return  # turn_in sem quest_id é sem sentido — rejeita direto
        eid = session.entity_id
        from engine.components import QuestLog
        ql = self.world_server.world.get_component(eid, QuestLog)
        if ql is None:
            return
        import engine.quest_logic as quest_logic
        # talk_to_npc antes de can_turn_in — garante que objetivos do tipo
        # talk_to_npc (ex: "fale com o NPC antes de entregar") já estejam
        # contabilizados quando a validação de entrega rodar.
        npc_name = payload.get("npc_name", "")
        if npc_name:
            quest_logic.apply_event(ql, "talk_to_npc", {"npc_name": npc_name})
        if not quest_logic.can_turn_in(ql, qid):
            return
        reward = quest_logic.complete_quest(self.world_server.world, eid, ql, qid)
        if reward is None:
            return

        if reward.xp > 0:
            from engine.components import CharacterStats, CombatStats, PermanentStats, TalentTree
            char = self.world_server.world.get_component(eid, CharacterStats)
            cs   = self.world_server.world.get_component(eid, CombatStats)
            perm = self.world_server.world.get_component(eid, PermanentStats)
            if char and cs:
                self.world_server.queue_stats_update({
                    "player_eid": eid, "xp": reward.xp,
                })
                level_before = char.level
                char.current_xp += reward.xp
                from engine.stats_system import process_levelups
                process_levelups(self.world_server.world, eid, char, cs, perm)
                if char.level > level_before:
                    cs.current_hp = cs.max_hp
                    tt = self.world_server.world.get_component(eid, TalentTree)
                    self.world_server.queue_stats_update({
                        "player_eid":    eid,
                        "hp":            cs.current_hp,
                        "hp_max":        cs.max_hp,
                        "talent_points": tt.available_points if tt else 0,
                    })

        if reward.gold > 0:
            from engine.components import Wallet
            wall = self.world_server.world.get_component(eid, Wallet)
            if wall:
                wall.gold += reward.gold
                # Persistência já estava correta (get_player_save_data lê o
                # Wallet vivo), mas NADA avisava o cliente — diferente do
                # bloco de XP logo acima, que já chama queue_stats_update.
                # Jogador só via o gold certo no próximo relogin (bug real
                # reportado por testers: "quest dava X gold e não deu").
                self.world_server.queue_stats_update({
                    "player_eid": eid,
                    "gold":       wall.gold,
                })

        # Persiste imediatamente (mesmo padrão de TALENT_UPDATE) — crash do
        # servidor não perde a entrega que já concedeu XP/gold/itens.
        from server.auth import save_character
        srv_data = self.world_server.get_player_save_data(session.session_id)
        _live_eq = self.world_server.get_player_equipment_data(session.session_id)
        merged   = self._build_save_merge(srv_data, session.last_client_payload, _live_eq)
        try:
            await save_character(session.char_data["id"], merged)
        except Exception as e:
            log.error(f"[QuestTurnIn] ERRO ao salvar: {e}")

        await session.send(MsgType.QUEST_UPDATE, {
            "active":        {q: list(p) for q, p in ql.active.items()},
            "completed":     list(ql.completed),
            "completed_qid": qid,
        })

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
            # Broadcast: corpo some para todos no AOI (mesmo mapa)
            corpse_data = self.world_server._corpses.get(corpse_id, {})
            despawn_payload = {"eid": -corpse_id}
            for s in self._sessions_in_aoi(corpse_data.get("tx", 0),
                                           corpse_data.get("ty", 0),
                                           corpse_data.get("map")):
                await s.send(MsgType.ENTITY_DESPAWN, despawn_payload)
                s.known_eids.discard(-corpse_id)

    # cooldown per session_id: timestamp do último unstuck (módulo-level dict)
    _unstuck_cooldowns: "dict[str, float]" = {}
    _UNSTUCK_CD = 60.0  # segundos

    async def _handle_unstuck(self, session: Session, payload: dict, ts: int) -> None:
        """Teleporta o player para o spawn sem morte — usa-se quando ficou preso."""
        if not session.authenticated:
            return
        import time as _t
        sid = session.session_id
        now = _t.time()
        last = self._unstuck_cooldowns.get(sid, 0.0)
        remaining = self._UNSTUCK_CD - (now - last)
        if remaining > 0:
            await session.send(MsgType.ERROR, {"reason": f"unstuck_cooldown:{int(remaining)}"})
            return

        player_eid = self.world_server._player_eids.get(sid, -1)
        if player_eid == -1:
            return

        from server.respawn_system import RespawnMixin as _RS
        rx, ry = _RS.RESPAWN_TILE

        from engine.components import TileMovement, Position, StatusEffects, AIControlled, CombatState
        from shared.constants import TILE_SIZE

        # Teleporta no servidor — snap_to_tile cancela tween em andamento e
        # sincroniza pixels/Position (o write manual antigo não resetava
        # is_moving nem os campos de pixel).
        from engine.utils import snap_to_tile as _snap_tp
        _snap_tp(self.world_server.world, player_eid, rx, ry, carry_prev=False)

        # Limpa efeitos ativos (stun, root, DoT…)
        sfx = self.world_server.world.get_component(player_eid, StatusEffects)
        if sfx:
            sfx.effects.clear()
            # StatusEffectSystem.update() pula a entidade inteira quando
            # effects fica vazio (ver engine/core_systems.py) — sem resetar
            # aqui, slow_mult ficava PARADO no valor antigo (ex: teleporte
            # com um slow ativo) até o player ganhar um efeito novo.
            tm_tp = self.world_server.world.get_component(player_eid, TileMovement)
            if tm_tp:
                tm_tp.slow_mult = 1.0

        # Limpa aggro dos mobs
        for mob_eid in self.world_server._mob_eids:
            mob_cst = self.world_server.world.get_component(mob_eid, CombatState)
            if mob_cst and mob_cst.target_entity_id == player_eid:
                mob_cst.target_entity_id = -1
            mob_ai = self.world_server.world.get_component(mob_eid, AIControlled)
            if mob_ai and mob_ai.target_eid == player_eid:
                mob_ai.target_eid        = -1
                mob_ai.aggroed_by_damage = False
                mob_ai.state             = "RETURNING"
                mob_ai.path              = None

        # Agenda broadcast para AOI (outros players veem o teleporte)
        self.world_server._moved_this_tick.append({
            "eid": player_eid, "tx": rx, "ty": ry, "from_tx": rx, "from_ty": ry,
        })

        # Envia posição corrigida direto para o próprio jogador
        await session.send(MsgType.ENTITY_MOVE, {
            "eid": player_eid, "tx": rx, "ty": ry, "from_tx": rx, "from_ty": ry,
        })

        self._unstuck_cooldowns[sid] = now
        log.info(f"[Unstuck] {sid} teleportado para {rx},{ry}")

    async def _handle_release_spirit(self, session: Session, payload: dict, ts: int) -> None:
        """Player com corpo morto clicou 'Liberar espírito' — vira ghost no cemitério."""
        if not session.authenticated:
            return
        player_eid = self.world_server._player_eids.get(session.session_id)
        if player_eid is None:
            return
        self.world_server._handle_release_spirit(player_eid)

    async def _handle_revive_request(self, session: Session, payload: dict, ts: int) -> None:
        """Ghost dentro do raio do corpo clicou 'Sim' — revive com HP parcial no corpo."""
        if not session.authenticated:
            return
        player_eid = self.world_server._player_eids.get(session.session_id)
        if player_eid is None:
            return

        from engine.components import GhostState, TileMovement
        from shared.constants import GHOST_CORPSE_RADIUS_TILES, GHOST_CORPSE_REVIVE_HP_FRAC

        gst = self.world_server.world.get_component(player_eid, GhostState)
        tm  = self.world_server.world.get_component(player_eid, TileMovement)
        if not gst or not tm or not gst.is_ghost:
            return
        # Revalida distância no momento do request — não confia na flag cacheada
        if (abs(tm.current_tile_x - gst.corpse_tx) > GHOST_CORPSE_RADIUS_TILES
                or abs(tm.current_tile_y - gst.corpse_ty) > GHOST_CORPSE_RADIUS_TILES):
            return

        self.world_server._revive_player(player_eid, hp_frac=GHOST_CORPSE_REVIVE_HP_FRAC, at_corpse=True)

    # ── Helpers de spawn ─────────────────────────────────────────────────────

    async def _spawn_and_start(self, session: Session, char_data: dict) -> None:
        """Spawna o jogador no mundo e envia LOGIN_OK + WORLD_STATE."""
        char_data["client_ap"]     = 0.0
        char_data["client_max_hp"] = 0
        eid = self.world_server.spawn_player(session.session_id, char_data)
        # spawn_player pode ter corrigido map_id (ex: 'map_main' → mapa válido).
        # Atualiza char_data para que o cliente receba o mapa real.
        char_data["map_id"] = self.world_server.get_player_map(session.session_id)

        session.entity_id     = eid
        session.char_data     = dict(char_data)
        self._eid_to_sid[eid] = session.session_id

        tx, ty = self.world_server.get_tile_pos(session.session_id)
        srv_hp, srv_hp_max = self.world_server.get_player_hp(session.session_id)

        await session.send(MsgType.LOGIN_OK, {
            "token":     session.session_id,
            "eid":       eid,
            "char":      dict(char_data),
            "server_ts": int(time.time() * 1000),
            "hp":        srv_hp,
            "hp_max":    srv_hp_max,
        })

        near_players = self.world_server.get_players_in_aoi(session.session_id, AOI_RADIUS)
        for p in near_players:
            s2 = self._sessions.get(p.get("session_id", ""))
            if s2:
                _h, _hm = self.world_server.get_player_hp(s2.session_id)
                p.update({"name": s2.display_name,
                           "class_id": s2.char_data.get("class_id", "guerreiro"),
                           "hp": _h, "hp_max": _hm,
                           "level": s2.char_data.get("level", 1), "effects": []})

        near_mobs    = self.world_server.get_mobs_in_aoi(
            tx, ty, AOI_RADIUS,
            map_file=self.world_server.get_player_map(session.session_id),
        )
        all_entities = near_players + near_mobs
        await session.send(MsgType.WORLD_STATE, {
            "tick": self.world_server.tick_count, "tx": tx, "ty": ty,
            "entities": all_entities,
        })
        session.known_eids.add(eid)
        for ent in all_entities:
            session.known_eids.add(ent["eid"])

        _nh, _nhm = self.world_server.get_player_hp(session.session_id)
        await self._broadcast_aoi_except(session, MsgType.ENTITY_SPAWN, {
            "eid": eid, "kind": "player", "tx": tx, "ty": ty,
            "name":     session.display_name,
            "class_id": char_data.get("class_id", "guerreiro"),
            "hp": _nh, "hp_max": _nhm,
            "level":    char_data.get("level", 1), "effects": [],
        })
        for s in self._sessions.values():
            if s.authenticated and s.session_id != session.session_id:
                sx, sy = self.world_server.get_tile_pos(s.session_id)
                if _in_aoi(tx, ty, sx, sy, AOI_RADIUS):
                    s.known_eids.add(eid)
        log.info(f"[Session] entrou no jogo: {session.username!r}  eid={eid}  tile=({tx},{ty})")

    async def _handle_select_character(self, session: Session,
                                       payload: dict, ts: int) -> None:
        """Seleciona personagem existente para jogar."""
        if not session.authenticated or session.entity_id != -1:
            return
        char_id = int(payload.get("char_id", -1))
        if char_id < 0:
            await session.send(MsgType.CHARACTER_ERROR, {"reason": "invalid_char_id"})
            return
        from server.auth import get_character as _get_char
        char_data = await _get_char(session.account_id, char_id)
        if not char_data:
            await session.send(MsgType.CHARACTER_ERROR, {"reason": "char_not_found"})
            return
        await self._spawn_and_start(session, char_data)

    async def _handle_delete_character(self, session: Session,
                                       payload: dict, ts: int) -> None:
        """Exclui personagem da conta."""
        if not session.authenticated or session.entity_id != -1:
            return
        char_id = int(payload.get("char_id", -1))
        if char_id < 0:
            return
        from server.auth import delete_character as _del_char
        await _del_char(session.account_id, char_id)
        await session.send(MsgType.DELETE_CHARACTER_OK, {})

    async def _handle_create_character(self, session: Session,
                                       payload: dict, ts: int) -> None:
        """Cria personagem para conta autenticada que ainda não tem personagem."""
        if not session.authenticated:
            await session.send(MsgType.CHARACTER_ERROR, {"reason": "not_authenticated"})
            return
        if session.entity_id != -1:
            await session.send(MsgType.CHARACTER_ERROR, {"reason": "already_has_character"})
            return
        name     = str(payload.get("name",     "Aventureiro")).strip() or "Aventureiro"
        class_id = str(payload.get("class_id", "guerreiro"))
        if class_id not in ("guerreiro", "mago", "arqueiro"):
            class_id = "guerreiro"

        from server.auth import create_character as _create_char, _get_conn as _gc
        ok = await _create_char(session.account_id, name, class_id)
        if not ok:
            await session.send(MsgType.CHARACTER_ERROR, {"reason": "creation_failed"})
            return

        # Lê o personagem recém-criado (maior id da conta)
        def _fetch():
            with _gc() as conn:
                row = conn.execute(
                    "SELECT * FROM characters WHERE account_id=? ORDER BY id DESC LIMIT 1",
                    (session.account_id,)
                ).fetchone()
                return dict(row) if row else None

        char_data = await asyncio.get_running_loop().run_in_executor(None, _fetch)
        if not char_data:
            await session.send(MsgType.CHARACTER_ERROR, {"reason": "creation_failed"})
            return

        # Envia apenas CHARACTER_CREATED com dados do personagem.
        # O cliente volta à lista de seleção; o spawn ocorre só ao clicar "Jogar".
        await session.send(MsgType.CHARACTER_CREATED, {"char": dict(char_data)})

    async def _handle_zone_change_req(self, session: Session, payload: dict, ts: int = 0) -> None:
        """Processa pedido de troca de mapa C→S. Servidor valida e executa; envia ZONE_CHANGE.

        Modelo offline: o cliente detecta o tile de transição e envia o pedido — o servidor
        confia nessa detecção e apenas valida se o mapa destino está carregado. Checar a
        posição exata server-side causa falsos rejeitos por timing (MOVE em trânsito quando
        ZONE_CHANGE_REQ chega) ou quando o tile de transição é rejeitado pelo walkability
        do mapa errado. Não há vantagem de trapaça: o destino (to_map + target_x/y) é fixo
        no servidor — um cliente desonesto apenas chegaria ao mesmo tile de destino mais cedo.
        """
        if not session.authenticated or session.entity_id == -1:
            return
        to_map   = str(payload.get("to_map", ""))
        target_x = int(payload.get("target_x", 0))
        target_y = int(payload.get("target_y", 0))

        # Única validação: mapa destino deve estar carregado (evita teletransporte arbitrário).
        if to_map not in self.world_server._map_bundles:
            return

        # Executa a troca de mapa no servidor
        self.world_server.transfer_player(
            session.session_id, session.entity_id,
            to_map, target_x, target_y,
        )

        # Notifica o cliente para carregar o novo mapa
        await session.send(MsgType.ZONE_CHANGE, {
            "map_file": to_map,
            "target_x": target_x,
            "target_y": target_y,
        })
        # Invalida known_eids — entidades do mapa antigo não são mais visíveis
        session.known_eids.clear()

    # ── Trade (player↔player) — ver server/trade_processor.py ────────────────

    async def _broadcast_trade_state(self, trade_id: int) -> None:
        """Envia TRADE_STATE personalizado (my_*/their_* já resolvidos) pros
        dois lados de uma sessão de trade ativa."""
        trade_session = self.world_server._trade_sessions.get(trade_id)
        if trade_session is None:
            return
        for eid in (trade_session.player_a, trade_session.player_b):
            sid = self.world_server.get_session_id_for_player(eid)
            sess = self._sessions.get(sid) if sid else None
            if sess and sess.authenticated:
                await sess.send(MsgType.TRADE_STATE,
                                 self.world_server.build_trade_state_payload(trade_session, eid))

    async def _handle_trade_request(self, session: Session, payload: dict, ts: int) -> None:
        """Player pediu trade a um alvo remoto (Shift+clique no cliente)."""
        if not session.authenticated:
            return
        requester_eid = self.world_server._player_eids.get(session.session_id)
        if requester_eid is None:
            return
        target_eid = int(payload.get("target_eid", -1))
        reason = self.world_server.request_trade(requester_eid, target_eid)
        if reason is not None:
            await session.send(MsgType.TRADE_CANCELLED, {"trade_id": -1, "reason": reason})
            return
        target_sid = self.world_server.get_session_id_for_player(target_eid)
        target_session = self._sessions.get(target_sid) if target_sid else None
        if target_session and target_session.authenticated:
            await target_session.send(MsgType.TRADE_INVITE, {
                "from_eid":  requester_eid,
                "from_name": session.display_name,
            })

    async def _respond_trade_invite(self, session: Session, accept: bool) -> None:
        """Compartilhado por TRADE_ACCEPT/TRADE_DECLINE — só o valor de
        `accept` muda entre os dois handlers."""
        if not session.authenticated:
            return
        target_eid = self.world_server._player_eids.get(session.session_id)
        if target_eid is None:
            return
        requester_eid, trade_id = self.world_server.respond_trade_invite(target_eid, accept)
        if requester_eid is None:
            return  # não havia convite pendente — nada a fazer
        requester_sid = self.world_server.get_session_id_for_player(requester_eid)
        requester_session = self._sessions.get(requester_sid) if requester_sid else None
        if trade_id is None:
            reason = "declined" if not accept else "invalid"
            if requester_session and requester_session.authenticated:
                await requester_session.send(MsgType.TRADE_CANCELLED, {"trade_id": -1, "reason": reason})
            return
        await session.send(MsgType.TRADE_OPEN, {
            "trade_id":  trade_id,
            "other_eid": requester_eid,
            "other_name": requester_session.display_name if requester_session else "?",
        })
        if requester_session and requester_session.authenticated:
            await requester_session.send(MsgType.TRADE_OPEN, {
                "trade_id":   trade_id,
                "other_eid":  target_eid,
                "other_name": session.display_name,
            })

    async def _handle_trade_accept(self, session: Session, payload: dict, ts: int) -> None:
        await self._respond_trade_invite(session, True)

    async def _handle_trade_decline(self, session: Session, payload: dict, ts: int) -> None:
        await self._respond_trade_invite(session, False)

    # ── Duelo (mesmo formato do trade — ver server/duel_processor.py) ────────

    async def _handle_duel_request(self, session: Session, payload: dict, ts: int) -> None:
        """Botão "Duelar" do modal de interação com player."""
        if not session.authenticated:
            return
        requester_eid = self.world_server._player_eids.get(session.session_id)
        if requester_eid is None:
            return
        target_eid = int(payload.get("target_eid", -1))
        reason = self.world_server.request_duel(requester_eid, target_eid)
        if reason is not None:
            await session.send(MsgType.DUEL_END, {
                "winner_eid": -1, "loser_eid": -1, "reason": reason})
            return
        target_sid = self.world_server.get_session_id_for_player(target_eid)
        target_session = self._sessions.get(target_sid) if target_sid else None
        if target_session and target_session.authenticated:
            await target_session.send(MsgType.DUEL_INVITE, {
                "from_eid":  requester_eid,
                "from_name": session.display_name,
            })

    async def _respond_duel_invite(self, session: Session, accept: bool) -> None:
        if not session.authenticated:
            return
        target_eid = self.world_server._player_eids.get(session.session_id)
        if target_eid is None:
            return
        requester_eid, started = self.world_server.respond_duel_invite(target_eid, accept)
        if requester_eid is None:
            return  # não havia convite pendente
        requester_sid = self.world_server.get_session_id_for_player(requester_eid)
        requester_session = self._sessions.get(requester_sid) if requester_sid else None
        if not started:
            reason = "declined" if not accept else "invalid"
            if requester_session and requester_session.authenticated:
                await requester_session.send(MsgType.DUEL_END, {
                    "winner_eid": -1, "loser_eid": -1, "reason": reason})
            return
        await session.send(MsgType.DUEL_START, {
            "opponent_eid":  requester_eid,
            "opponent_name": requester_session.display_name if requester_session else "?",
        })
        if requester_session and requester_session.authenticated:
            await requester_session.send(MsgType.DUEL_START, {
                "opponent_eid":  target_eid,
                "opponent_name": session.display_name,
            })

    async def _handle_duel_accept(self, session: Session, payload: dict, ts: int) -> None:
        await self._respond_duel_invite(session, True)

    async def _handle_duel_decline(self, session: Session, payload: dict, ts: int) -> None:
        await self._respond_duel_invite(session, False)

    # ── Party/Grupo (mesmo formato do duelo — ver server/party_processor.py) ──

    async def _handle_party_invite(self, session: Session, payload: dict, ts: int) -> None:
        """Botão "Convidar p/ Grupo" do modal de interação OU comando de
        chat "/convidar" (client/party_handlers.py) — mesmo PARTY_INVITE."""
        if not session.authenticated:
            return
        requester_eid = self.world_server._player_eids.get(session.session_id)
        if requester_eid is None:
            return
        target_eid = int(payload.get("target_eid", -1))
        reason = self.world_server.request_party_invite(requester_eid, target_eid)
        if reason is not None:
            await session.send(MsgType.PARTY_INVITE_FAILED, {"reason": reason})
            return
        target_sid = self.world_server.get_session_id_for_player(target_eid)
        target_session = self._sessions.get(target_sid) if target_sid else None
        if target_session and target_session.authenticated:
            await target_session.send(MsgType.PARTY_INVITE_RECEIVED, {
                "from_eid":  requester_eid,
                "from_name": session.display_name,
            })

    async def _respond_party_invite(self, session: Session, accept: bool) -> None:
        if not session.authenticated:
            return
        target_eid = self.world_server._player_eids.get(session.session_id)
        if target_eid is None:
            return
        requester_eid, started = self.world_server.respond_party_invite(target_eid, accept)
        if requester_eid is None:
            return  # não havia convite pendente
        if not started:
            requester_sid = self.world_server.get_session_id_for_player(requester_eid)
            requester_session = self._sessions.get(requester_sid) if requester_sid else None
            reason = "declined" if not accept else "invalid"
            if requester_session and requester_session.authenticated:
                await requester_session.send(MsgType.PARTY_INVITE_FAILED, {"reason": reason})
            return
        # PARTY_STATE pros membros vai pelo broadcast loop do tick
        # (consume_party_state_events) — não precisa mandar nada aqui.

    async def _handle_party_accept(self, session: Session, payload: dict, ts: int) -> None:
        await self._respond_party_invite(session, True)

    async def _handle_party_decline(self, session: Session, payload: dict, ts: int) -> None:
        await self._respond_party_invite(session, False)

    async def _handle_party_leave(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        self.world_server.leave_party(eid)

    async def _handle_party_kick(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        leader_eid = self.world_server._player_eids.get(session.session_id)
        if leader_eid is None:
            return
        target_eid = int(payload.get("target_eid", -1))
        self.world_server.kick_from_party(leader_eid, target_eid)

    async def _handle_trade_offer_item(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        player_eid = self.world_server._player_eids.get(session.session_id)
        if player_eid is None:
            return
        trade_id = self.world_server._player_trade.get(player_eid)
        if trade_id is None:
            return
        inv_index = int(payload.get("inv_index", -1))
        if self.world_server.add_trade_item(player_eid, inv_index) is None:
            await self._broadcast_trade_state(trade_id)

    async def _handle_trade_withdraw_item(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        player_eid = self.world_server._player_eids.get(session.session_id)
        if player_eid is None:
            return
        trade_id = self.world_server._player_trade.get(player_eid)
        if trade_id is None:
            return
        offer_slot = int(payload.get("offer_slot", -1))
        if self.world_server.withdraw_trade_item(player_eid, offer_slot) is None:
            await self._broadcast_trade_state(trade_id)

    async def _handle_trade_set_gold(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        player_eid = self.world_server._player_eids.get(session.session_id)
        if player_eid is None:
            return
        trade_id = self.world_server._player_trade.get(player_eid)
        if trade_id is None:
            return
        amount = int(payload.get("amount", -1))
        if self.world_server.set_trade_gold(player_eid, amount) is None:
            await self._broadcast_trade_state(trade_id)

    async def _handle_trade_confirm(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        player_eid = self.world_server._player_eids.get(session.session_id)
        if player_eid is None:
            return
        trade_id = self.world_server._player_trade.get(player_eid)
        if trade_id is None:
            return
        status, trade_session = self.world_server.confirm_trade(player_eid)
        if status == "state":
            await self._broadcast_trade_state(trade_id)
        elif status == "executed":
            for eid, received_items, received_gold in (
                    (trade_session.player_a, trade_session.offer_b, trade_session.gold_b),
                    (trade_session.player_b, trade_session.offer_a, trade_session.gold_a)):
                sid = self.world_server.get_session_id_for_player(eid)
                sess = self._sessions.get(sid) if sid else None
                if sess and sess.authenticated:
                    await sess.send(MsgType.TRADE_RESULT, {
                        "trade_id":       trade_id,
                        "received_items": [self.world_server._item_data_from_obj(i) for i in received_items],
                        "received_gold":  received_gold,
                    })
        elif status == "inventory_full":
            for eid in (trade_session.player_a, trade_session.player_b):
                sid = self.world_server.get_session_id_for_player(eid)
                sess = self._sessions.get(sid) if sid else None
                if sess and sess.authenticated:
                    await sess.send(MsgType.TRADE_CANCELLED, {"trade_id": trade_id, "reason": "inventory_full"})

    async def _handle_trade_cancel(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        player_eid = self.world_server._player_eids.get(session.session_id)
        if player_eid is None:
            return
        trade_session = self.world_server.get_trade_session(player_eid)
        if trade_session is None:
            return
        other_eid = trade_session.other_of(player_eid)
        trade_id = self.world_server.cancel_trade(player_eid, "cancelled")
        if trade_id is None:
            return
        await session.send(MsgType.TRADE_CANCELLED, {"trade_id": trade_id, "reason": "cancelled"})
        other_sid = self.world_server.get_session_id_for_player(other_eid)
        other_session = self._sessions.get(other_sid) if other_sid else None
        if other_session and other_session.authenticated:
            await other_session.send(MsgType.TRADE_CANCELLED, {"trade_id": trade_id, "reason": "cancelled"})

    _handlers = {
        MsgType.REGISTER:           _handle_register,
        MsgType.CREATE_CHARACTER:   _handle_create_character,
        MsgType.SELECT_CHARACTER:   _handle_select_character,
        MsgType.DELETE_CHARACTER:   _handle_delete_character,
        MsgType.LOGIN:        _handle_login,
        MsgType.LOGOUT:       _handle_logout,
        MsgType.MOVE:         _handle_move,
        MsgType.PING:         _handle_ping,
        MsgType.AUTO_ATTACK:  _handle_auto_attack,
        MsgType.CAST_SKILL:        _handle_cast_skill,
        MsgType.CANCEL_CAST:       _handle_cancel_cast,
        MsgType.CAST_DIR_UPDATE:   _handle_cast_dir_update,
        MsgType.PROJECTILE_HIT_CS: _handle_projectile_hit,
        MsgType.CHAT_SEND:         _handle_chat,
        MsgType.LOOT_REQUEST:      _handle_loot_request,
        MsgType.SAVE_STATE:        _handle_save_state,
        MsgType.PLAYER_STAT_SYNC:  _handle_player_stat_sync,
        MsgType.CONSUMABLE_USE:    _handle_consumable_use,
        MsgType.BUY_REQUEST:     _handle_buy_request,
        MsgType.SELL_REQUEST:      _handle_sell_request,
        MsgType.PLAYER_HP_SYNC:    _handle_player_hp_sync,
        MsgType.EQUIP_SYNC:        _handle_equip_sync,
        MsgType.GOLD_UPDATE:       _handle_gold_update,
        MsgType.INV_SYNC:  _handle_inventory_update,
        MsgType.TALENT_UPDATE:     _handle_talent_update,
        MsgType.HOTBAR_UPDATE:     _handle_hotbar_update,
        MsgType.UNSTUCK:           _handle_unstuck,
        MsgType.RELEASE_SPIRIT:    _handle_release_spirit,
        MsgType.REVIVE_REQUEST:    _handle_revive_request,
        MsgType.QUEST_ACCEPT:      _handle_quest_accept,
        MsgType.QUEST_TURN_IN:     _handle_quest_turn_in,
        MsgType.ZONE_CHANGE_REQ:   _handle_zone_change_req,
        MsgType.TRADE_REQUEST:       _handle_trade_request,
        MsgType.TRADE_ACCEPT:        _handle_trade_accept,
        MsgType.TRADE_DECLINE:       _handle_trade_decline,
        MsgType.TRADE_OFFER_ITEM:    _handle_trade_offer_item,
        MsgType.TRADE_WITHDRAW_ITEM: _handle_trade_withdraw_item,
        MsgType.TRADE_SET_GOLD:      _handle_trade_set_gold,
        MsgType.TRADE_CONFIRM:       _handle_trade_confirm,
        MsgType.TRADE_CANCEL:        _handle_trade_cancel,
        MsgType.DUEL_REQUEST:        _handle_duel_request,
        MsgType.DUEL_ACCEPT:         _handle_duel_accept,
        MsgType.DUEL_DECLINE:        _handle_duel_decline,
        MsgType.PARTY_INVITE:        _handle_party_invite,
        MsgType.PARTY_ACCEPT:        _handle_party_accept,
        MsgType.PARTY_DECLINE:       _handle_party_decline,
        MsgType.PARTY_LEAVE:         _handle_party_leave,
        MsgType.PARTY_KICK:          _handle_party_kick,
    }

    # ── AOI subscription — núcleo do sistema ─────────────────────────────────

    def _on_tick(self, tick_count: int, deltas: dict) -> None:
        if tick_count % (TICK_RATE * 300) == 0 and tick_count > 0:  # autosave a cada 5 min
            asyncio.create_task(self._autosave_all())
        # Dispatch sempre que há conteúdo — inclui skill_results que não entram em deltas
        has_pending = (any(deltas.values())
                       or bool(self.world_server._skill_results_this_tick)
                       or bool(self.world_server._skill_effects_this_tick)
                       or bool(self.world_server._pending_loot_notifications)
                       or bool(self.world_server._pending_stats_updates)
                       or bool(self.world_server._expired_corpses_this_tick)
                       or bool(self.world_server._player_hp_broadcasts_this_tick)
                       or bool(self.world_server._skill_levels_broadcasts_this_tick)
                       or bool(self.world_server._quest_update_broadcasts_this_tick)
                       or bool(self.world_server._pending_sound_events))
        if not has_pending:
            return
        asyncio.create_task(self._dispatch_tick_deltas(deltas))

    async def _dispatch_tick_deltas(self, deltas: dict) -> None:
        """Distribui deltas para cada cliente respeitando known_eids (AOI subscription)."""
        try:
            # Resultados de skills ANTES do AOI_UPDATE.
            # _sessions_in_aoi: mesmo mapa do caster + visibilidade (caster
            # camuflado não vaza eventos pra quem não o vê; o dono sempre recebe).
            for skill_result in self.world_server.consume_skill_results():
                caster_eid = skill_result["caster_eid"]
                caster_sid = self.world_server.get_session_id_for_player(caster_eid)
                if not caster_sid:
                    continue
                cx, cy = self.world_server.get_tile_pos(caster_sid)
                _caster_map = self.world_server.get_entity_map(caster_eid)
                for s in self._sessions_in_aoi(cx, cy, _caster_map,
                                               origin_eid=caster_eid):
                    await s.send(MsgType.SKILL_RESULT, skill_result)

            # Efeitos de apresentação (som/VFX) — broadcast AOI separado do gameplay
            for skill_effect in self.world_server.consume_skill_effects():
                _eff_tx  = skill_effect.get("tx", 0)
                _eff_ty  = skill_effect.get("ty", 0)
                _eff_eid = skill_effect.get("caster_eid", -1)
                _eff_map = self.world_server.get_entity_map(_eff_eid)
                for s in self._sessions_in_aoi(_eff_tx, _eff_ty, _eff_map,
                                               origin_eid=_eff_eid):
                    await s.send(MsgType.SKILL_EFFECT, skill_effect)

            # Pré-calcula posições de todos os mobs UMA VEZ por tick.
            # Elimina O(mobs) component lookups por player por tick no sweep de AOI.
            # SpatialHash reduz o sweep de O(P×M) para O(P × candidatos_no_AOI).
            from engine.components import TileMovement as _TM_aoi
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
                    ok = await session.send(MsgType.AOI_UPDATE, update)
                    if not ok:
                        # send falhou DEPOIS de _build_update_for_session já ter
                        # mutado known_eids (spawn/despawn) assumindo entrega —
                        # sem isso, o servidor acha que o cliente já conhece
                        # entidades que na real nunca chegaram lá, e todo MOVE
                        # seguinte pra elas é descartado no cliente pra sempre.
                        # Reverter a mutação cirurgicamente não é viável aqui
                        # (entrelaçada com o cálculo do payload); em vez disso,
                        # zera known_eids — o próximo tick com send bem-sucedido
                        # trata TUDO que está no raio como "novo" e reenvia os
                        # ENTITY_SPAWN, resincronizando sozinho.
                        session.known_eids.clear()

            # Mortes de players APÓS AOI_UPDATE: garante que PLAYER_DEATH chega
            # depois do COMBAT_RESULT (hp_after=0) do golpe fatal, sobrescrevendo HP.
            await self._send_player_deaths(deltas)
            await self._send_entity_deaths(deltas)
            await self._send_player_revives(deltas)
            await self._send_ghost_state_updates(deltas)

            # STATS_UPDATE privados (XP, HP/mana/rage sync, heals, projéteis...).
            # Canal tipado via WorldServer.queue_stats_update — schema documentado
            # lá (problema G resolvido: era o "bag" _pending_xp_deliveries com
            # 16 produtores fazendo append direto e placeholders obrigatórios).
            # Forwarding total: qualquer campo do produtor chega ao cliente
            # (que lê com .get() e defaults) — campo novo não precisa de
            # espelho manual aqui.
            for xp_entry in self.world_server.consume_stats_updates():
                sid = self.world_server.get_session_id_for_player(xp_entry["player_eid"])
                if sid:
                    session = self._sessions.get(sid)
                    if session and session.authenticated:
                        _payload = dict(xp_entry)
                        _payload["eid"]       = _payload.pop("player_eid")
                        _payload["xp_gained"] = _payload.pop("xp", 0)
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
                for s in self._sessions_in_aoi(notif_tx, notif_ty, notif.get("map")):
                    await s.send(MsgType.ENTITY_SPAWN, spawn_payload)
                    s.known_eids.add(-corpse_id)

                # 2. LOOT_AVAILABLE ao dono E, se ele estiver em grupo, a todo
                # o grupo (free-for-all — decisão do usuário 17/07/2026;
                # request_loot() já autoriza qualquer membro do mesmo grupo
                # do dono, isto aqui é só quem recebe o aviso/lista de itens).
                _loot_recipients = self.world_server.get_party_members(owner_eid) or [owner_eid]
                for _loot_eid in _loot_recipients:
                    _loot_sid = self.world_server.get_session_id_for_player(_loot_eid)
                    _loot_sess = self._sessions.get(_loot_sid) if _loot_sid else None
                    if _loot_sess and _loot_sess.authenticated:
                        await _loot_sess.send(MsgType.LOOT_AVAILABLE, {
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
                    # bcast_tx/ty (ex: respawn pós-morte PvP) sobrescreve a posição
                    # atual — sem isso o filtro de AOI usaria o tile de respawn
                    # (longe de quem matou), e o restore de HP nunca chegaria a ele.
                    if "bcast_tx" in _hp_upd:
                        _cx, _cy = _hp_upd["bcast_tx"], _hp_upd["bcast_ty"]
                    else:
                        _cx, _cy = self.world_server.get_tile_pos(_caster_sid) if _caster_sid else (0, 0)
                    _hp_payload = {"eid": _caster_eid, "hp": _hp_upd["hp"], "hp_max": _hp_upd["hp_max"]}
                    if "level" in _hp_upd:
                        _hp_payload["level"] = _hp_upd["level"]
                    # exclude_sid: não envia ao próprio caster (já tem via STATS_UPDATE)
                    _hp_map = self.world_server.get_entity_map(_caster_eid)
                    for s in self._sessions_in_aoi(_cx, _cy, _hp_map,
                                                   origin_eid=_caster_eid,
                                                   exclude_sid=_caster_sid):
                        await s.send(MsgType.STATS_UPDATE, _hp_payload)

            # SkillLevels: só pro dono — progressão é privada, nunca broadcast AOI.
            for _skl_upd in self.world_server.consume_skill_levels_broadcasts():
                _skl_sid = self.world_server.get_session_id_for_player(_skl_upd["eid"])
                _skl_sess = self._sessions.get(_skl_sid) if _skl_sid else None
                if _skl_sess and _skl_sess.authenticated:
                    _skl_payload = {
                        "levels": _skl_upd["levels"],
                        "xp":     _skl_upd["xp"],
                    }
                    if "leveled_up" in _skl_upd:
                        _skl_payload["leveled_up"] = _skl_upd["leveled_up"]
                    await _skl_sess.send(MsgType.SKILL_LEVELS_UPDATE, _skl_payload)

            # QuestLog: só pro dono — progresso de quest é privado, nunca broadcast AOI.
            for _ql_upd in self.world_server.consume_quest_update_broadcasts():
                _ql_sid = self.world_server.get_session_id_for_player(_ql_upd["eid"])
                _ql_sess = self._sessions.get(_ql_sid) if _ql_sid else None
                if _ql_sess and _ql_sess.authenticated:
                    await _ql_sess.send(MsgType.QUEST_UPDATE, {
                        "active":    _ql_upd["active"],
                        "completed": _ql_upd["completed"],
                    })

            # Corpses que expiraram — notifica todos no AOI para remover visualmente
            for expired in self.world_server.consume_expired_corpses():
                cid = expired["cid"]
                despawn_payload = {"eid": -cid}
                for s in self._sessions_in_aoi(expired["tx"], expired["ty"],
                                               expired.get("map")):
                    await s.send(MsgType.ENTITY_DESPAWN, despawn_payload)
                    s.known_eids.discard(-cid)

            # Trades cancelados por distância neste tick — avisa os dois lados
            for _trd_cancel in self.world_server.consume_trade_cancellations():
                for _trd_eid in (_trd_cancel["player_a"], _trd_cancel["player_b"]):
                    _trd_sid = self.world_server.get_session_id_for_player(_trd_eid)
                    _trd_sess = self._sessions.get(_trd_sid) if _trd_sid else None
                    if _trd_sess and _trd_sess.authenticated:
                        await _trd_sess.send(MsgType.TRADE_CANCELLED, {
                            "trade_id": _trd_cancel["trade_id"],
                            "reason":   _trd_cancel["reason"],
                        })

            # Duelos encerrados neste tick (golpe letal/distância/logout) —
            # avisa os dois lados (win chega junto do COMBAT_RESULT do golpe)
            for _duel_end in self.world_server.consume_duel_end_events():
                for _duel_eid in (_duel_end["player_a"], _duel_end["player_b"]):
                    _duel_sid = self.world_server.get_session_id_for_player(_duel_eid)
                    _duel_sess = self._sessions.get(_duel_sid) if _duel_sid else None
                    if _duel_sess and _duel_sess.authenticated:
                        await _duel_sess.send(MsgType.DUEL_END, {
                            "winner_eid": _duel_end["winner_eid"],
                            "loser_eid":  _duel_end["loser_eid"],
                            "reason":     _duel_end["reason"],
                        })
                # Golpe letal: anuncia na aba Local pra QUALQUER UM na área
                # (pedido do usuário 16/07/2026 — não só os dois duelistas,
                # quem está por perto também vê). tx/ty/map vêm da posição
                # do vencedor no momento do golpe (duel_processor.end_duel).
                if _duel_end["reason"] == "win":
                    _dw_sid  = self.world_server.get_session_id_for_player(_duel_end["winner_eid"])
                    _dl_sid  = self.world_server.get_session_id_for_player(_duel_end["loser_eid"])
                    _dw_sess = self._sessions.get(_dw_sid) if _dw_sid else None
                    _dl_sess = self._sessions.get(_dl_sid) if _dl_sid else None
                    _dw_name = _dw_sess.display_name if _dw_sess else "?"
                    _dl_name = _dl_sess.display_name if _dl_sess else "?"
                    _duel_chat_msg = {
                        "sender":  "Sistema",
                        "text":    f"{_dw_name} venceu {_dl_name} em um duelo!",
                        "channel": "local",
                        "color":   [255, 200, 80],
                    }
                    for s in self._sessions_in_aoi(_duel_end["tx"], _duel_end["ty"],
                                                   _duel_end["map"]):
                        await s.send(MsgType.CHAT_MESSAGE, _duel_chat_msg)

            # Grupo mudou neste tick (convite aceito, saída, expulsão,
            # promoção de líder, disconnect) — avisa todo mundo afetado.
            # Item é um party_id (int, grupo mudou — manda PARTY_STATE pra
            # cada membro ATUAL) ou uma tupla ("left", eid) (eid saiu/foi
            # expulso e não está em nenhum grupo mais — PARTY_STATE vazio
            # só pra ele, se ainda estiver conectado).
            for _pty_ev in self.world_server.consume_party_state_events():
                if isinstance(_pty_ev, tuple):
                    _pty_left_eid = _pty_ev[1]
                    _pty_left_sid = self.world_server.get_session_id_for_player(_pty_left_eid)
                    _pty_left_sess = self._sessions.get(_pty_left_sid) if _pty_left_sid else None
                    if _pty_left_sess and _pty_left_sess.authenticated:
                        await _pty_left_sess.send(MsgType.PARTY_STATE, {
                            "party_id": -1, "leader_eid": -1, "members": [],
                        })
                    continue
                _pty_snap = self.world_server.get_party_snapshot(_pty_ev)
                if _pty_snap is None:
                    continue
                for _pty_member in _pty_snap["members"]:
                    _pty_sid = self.world_server.get_session_id_for_player(_pty_member["eid"])
                    _pty_sess = self._sessions.get(_pty_sid) if _pty_sid else None
                    if _pty_sess and _pty_sess.authenticated:
                        await _pty_sess.send(MsgType.PARTY_STATE, _pty_snap)

            # Eventos de som posicionais (aggro de mob, etc.) → broadcast AOI
            for _snd_ev in self.world_server.consume_sound_events():
                _snd_map = self.world_server.get_entity_map(_snd_ev.get("mob_eid", -1))
                for s in self._sessions_in_aoi(_snd_ev.get("tx", 0),
                                               _snd_ev.get("ty", 0), _snd_map):
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
                        if pos_corr.get("is_dash"):
                            _move_payload["is_dash"] = True
                        if "duration" in pos_corr:
                            _move_payload["duration"] = pos_corr["duration"]
                        await p_session.send(MsgType.ENTITY_MOVE, _move_payload)

        except Exception as e:
            import traceback
            log.error(f"[Session] ERRO em _dispatch_tick_deltas: {e}")
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
        r      = AOI_RADIUS
        r_exit = AOI_RADIUS + AOI_EXIT_BUFFER
        result: dict = {}

        # Mapa atual do player que recebe este update.
        _my_map = self.world_server.get_player_map(session.session_id)

        from engine.components import MapLocation as _ML_aoi

        def in_aoi(tx: int, ty: int, eid: int = -1) -> bool:
            # Métrica canônica: Chebyshev (utils.in_aoi) — mesma dos broadcasts
            # diretos (_sessions_in_aoi) e do spawn inicial (WORLD_STATE).
            # Antes era círculo Euclidiano só aqui: entidades nos "cantos" do
            # quadrado entravam por um critério e não pelo outro.
            if not _in_aoi(cx, cy, tx, ty, r):
                return False
            # MapLocation é a fonte única de verdade para entity→mapa (P3).
            # Entidade SEM MapLocation é excluída — toda entidade networked deve ter um.
            if eid >= 0:
                _ml = self.world_server.world.get_component(eid, _ML_aoi)
                if not _ml or _ml.map_file != _my_map:
                    return False
            return True

        def in_aoi_exit(tx: int, ty: int, eid: int = -1) -> bool:
            # Histerese: raio de SAÍDA maior que o de ENTRADA (AOI_EXIT_BUFFER).
            # Sem isso, uma entidade já conhecida cujo caminho "raspa" a borda
            # de AOI_RADIUS (ex: mob RETURNING cruzando perto de 15 tiles)
            # gerava despawn/respawn repetido a cada tick que cruzasse a
            # fronteira — nunca ficava visível tempo suficiente pro cliente
            # renderizar de forma estável. Entrada continua usando in_aoi()
            # (raio normal) — só a permanência usa o raio com buffer.
            if not _in_aoi(cx, cy, tx, ty, r_exit):
                return False
            if eid >= 0:
                _ml = self.world_server.world.get_component(eid, _ML_aoi)
                if not _ml or _ml.map_file != _my_map:
                    return False
            return True

        # ── Moves: verifica entradas/saídas de AOI ────────────────────
        confirmed_moves = []
        aoi_exits       = []
        aoi_entries     = []

        for m in deltas.get("moved", []):
            eid    = m["eid"]
            in_new = in_aoi(m["tx"],      m["ty"],      eid)
            in_old = in_aoi(m["from_tx"], m["from_ty"], eid)

            if eid in session.known_eids:
                if not _can_see(self.world_server.world, session.entity_id, eid):
                    # Entidade ficou invisível (ex: ghost liberado sem despawn explícito)
                    aoi_exits.append(eid)
                    session.known_eids.discard(eid)
                elif in_aoi_exit(m["tx"], m["ty"], eid):
                    confirmed_moves.append(m)   # ainda dentro do raio de saída (com buffer)
                else:
                    aoi_exits.append(eid)       # saiu do AOI (além do buffer)
                    session.known_eids.discard(eid)
            else:
                if in_new:
                    aoi_entries.append(eid)     # entrou no AOI pela primeira vez (raio normal)

        # ── Mudanças de visibilidade (ex: Camuflagem) ──────────────────
        # Sem isso, um player que camufla parado nunca some pros outros: o
        # check de _can_see() acima só roda para eids presentes em "moved"
        # deste tick, e quem não andou nunca gera esse delta.
        if deltas.get("visibility_changed"):
            from engine.components import TileMovement as _VisTM
            for eid in deltas["visibility_changed"]:
                if eid == session.entity_id:
                    continue
                vis_tm = self.world_server.world.get_component(eid, _VisTM)
                if not vis_tm:
                    continue
                vis_in_aoi  = in_aoi(vis_tm.current_tile_x, vis_tm.current_tile_y, eid)
                vis_can_see = _can_see(self.world_server.world, session.entity_id, eid)
                if eid in session.known_eids:
                    if not vis_can_see:
                        aoi_exits.append(eid)
                        session.known_eids.discard(eid)
                elif vis_in_aoi and vis_can_see:
                    aoi_entries.append(eid)

        # ── Novas entidades no AOI (via move) ─────────────────────────
        for eid in aoi_entries:
            if not _can_see(self.world_server.world, session.entity_id, eid):
                continue
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
            if sp["eid"] == session.entity_id:
                continue  # próprio player não precisa de spawn de si mesmo
            if in_aoi(sp["tx"], sp["ty"], sp["eid"]):
                result.setdefault("spawned", []).append(sp)
                session.known_eids.add(sp["eid"])

        # ── Despawns ──────────────────────────────────────────────────
        final_despawned  = list(aoi_exits)
        _death_positions = deltas.get("despawned_pos", {})  # eid→(tx,ty)
        # Mortes reais (mob hp=0, removido do mundo) — distinguido de saídas de AOI
        # para que o cliente só toque som de morte para kills reais, não para mobs
        # que simplesmente saíram do raio de 15 tiles (ex: RETURNING ao spawn).
        _real_death_eids: set[int] = set()
        for eid in deltas.get("despawned", []):
            if eid in session.known_eids:
                final_despawned.append(eid)
                session.known_eids.discard(eid)
                _real_death_eids.add(eid)
            else:
                # Mob morreu dentro do AOI mas cliente ainda não sabia dele
                # (ex: mob entrou/foi teleportado para perto do player e morto no mesmo tick)
                death_pos = _death_positions.get(eid)
                if death_pos and in_aoi(death_pos[0], death_pos[1], eid):
                    final_despawned.append(eid)
                    _real_death_eids.add(eid)

        # ── Combat: filtra eventos relevantes para o AOI desta sessão ─
        # Inclui eids recém-removidos de known_eids neste build (despawn/saída
        # de AOI no mesmo tick): sem isso, o evento de combate do golpe fatal
        # (mob despawna no mesmo tick) seria descartado, pois o discard acima
        # roda ANTES deste filtro — mob "desaparece" sem mostrar o dano final.
        # Inclui o próprio EID do player para receber eventos de cura/dano onde
        # o player é alvo (ex: regen de polimorfia, DoT de inimigo). O EID do
        # dono nunca está em known_eids (que rastreia apenas entidades externas).
        _combat_known = session.known_eids | set(final_despawned) | {session.entity_id}
        combat_events = [
            cr for cr in deltas.get("combat", [])
            if cr.get("target") in _combat_known
            or cr.get("attacker") in _combat_known
        ]

        # DEBUG C15/C16: combat event enviado com apenas UM dos lados (atacante/alvo)
        # dentro do AOI/known_eids desta sessão — o lado de fora pode estar em fog
        # para este cliente, mas ainda assim gera som/FLT na posição dele.
        if combat_events:
            from debug.aoi_debug import AOI_DBG as _AOI_DBG_combat, DBG_ENABLED as _DBG_EN_combat
            if _DBG_EN_combat:
                from engine.components import Position as _PosCombatDbg
                for _cr in combat_events:
                    _atk = _cr.get("attacker")
                    _tgt = _cr.get("target")
                    _atk_known = _atk in _combat_known
                    _tgt_known = _tgt in _combat_known
                    if _atk_known != _tgt_known:
                        _atk_pos = self.world_server.world.get_component(_atk, _PosCombatDbg)
                        _tgt_pos = self.world_server.world.get_component(_tgt, _PosCombatDbg)
                        _AOI_DBG_combat.log(
                            "COMBAT_ASYM", session=session.session_id,
                            cx=cx, cy=cy,
                            attacker=_atk, attacker_known=_atk_known,
                            attacker_pos=(getattr(_atk_pos, "x", None), getattr(_atk_pos, "y", None)),
                            target=_tgt, target_known=_tgt_known,
                            target_pos=(getattr(_tgt_pos, "x", None), getattr(_tgt_pos, "y", None)),
                            source=_cr.get("source"), sid=_cr.get("sid"),
                        )

        # DEBUG Bug2: para mobs despawnados neste tick, registra se o
        # combat event correspondente sobreviveu ao filtro de AOI acima.
        if deltas.get("despawned"):
            from debug.mob_combat_debug import MCL as _MCL_aoi
            if _MCL_aoi.DBG_ENABLED:
                for _eid_fh in deltas["despawned"]:
                    _evs_all = [c for c in deltas.get("combat", [])
                                if c.get("target") == _eid_fh]
                    if not _evs_all:
                        continue
                    _evs_sent = [c for c in combat_events if c.get("target") == _eid_fh]
                    _MCL_aoi.log("FATAL_SENT", _eid_fh, "?", "?", "",
                                  session=session.session_id,
                                  in_known_eids=(_eid_fh in session.known_eids),
                                  in_final_despawned=(_eid_fh in final_despawned),
                                  n_events=len(_evs_all), n_sent=len(_evs_sent))

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
        if _real_death_eids: result["died_eids"] = list(_real_death_eids)
        if combat_events:    result["combat"]    = combat_events
        if my_effects:       result["effects"]   = my_effects
        if mob_effects:      result["mob_effects"] = mob_effects

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
            if pos and in_aoi(pos[0], pos[1], mob_eid):
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
            if other_eid in final_despawned:
                continue  # despawned neste tick — não re-spawnar no mesmo frame
            if not _can_see(self.world_server.world, session.entity_id, other_eid):
                continue
            ox, oy = self.world_server.get_tile_pos(other_session.session_id)
            if in_aoi(ox, oy, other_eid):
                _hp, _hp_max = self.world_server.get_player_hp(other_session.session_id)
                from engine.components import GhostState as _OtherGST2, CharacterStats as _OtherCharAOI
                _ogst = self.world_server.world.get_component(other_eid, _OtherGST2)
                # level: componente VIVO, não other_session.char_data (snapshot
                # de login, nunca atualizado — mesma causa raiz do bug corrigido
                # em _sync_player_hp_dirty acima; sem isso, um player visto pela
                # primeira vez DEPOIS de subir de nível ainda mostraria o level
                # antigo aqui, mesmo com o fix do broadcast em tempo real).
                _ochar_aoi = self.world_server.world.get_component(other_eid, _OtherCharAOI)
                spawn_payload = {
                    "eid":      other_eid,
                    "kind":     "player",
                    "tx":       ox, "ty": oy,
                    "name":     other_session.display_name,
                    "class_id": other_session.char_data.get("class_id", "guerreiro"),
                    "hp":       _hp,
                    "hp_max":   _hp_max,
                    "level":    _ochar_aoi.level if _ochar_aoi else 1,
                    "effects":  [],
                    "is_ghost": bool(_ogst and _ogst.is_ghost),
                }
                result.setdefault("spawned", []).append(spawn_payload)
                session.known_eids.add(other_eid)

        # ── Player corpses: corpos não rastreados por mob_positions ──────
        # Necessário para que um ghost veja o próprio corpo ao se aproximar,
        # já que o one-shot de _spawned_this_tick foi enviado quando o ghost
        # estava no cemitério (longe do local da morte).
        from server.respawn_system import PLAYER_CORPSE_EID_BASE as _PCEB
        from engine.components import CharacterStats as _CharSweep
        for _cpeid, _cpdata in list(getattr(self.world_server, "_player_corpses", {}).items()):
            _seid = _PCEB + _cpeid
            if _seid in session.known_eids:
                continue
            if isinstance(_cpdata, tuple):
                _ctx, _cty = _cpdata
                _cchar = self.world_server.world.get_component(_cpeid, _CharSweep)
                _cname = _cchar.name     if _cchar else ""
                _ccls  = _cchar.class_id if _cchar else ""
            else:
                _ctx, _cty = _cpdata["tx"], _cpdata["ty"]
                _cname = _cpdata.get("name", "")
                _ccls  = _cpdata.get("class_id", "")
            if in_aoi(_ctx, _cty):
                result.setdefault("spawned", []).append({
                    "eid":      _seid,
                    "kind":     "player_corpse",
                    "tx":       _ctx, "ty": _cty,
                    "name":     _cname,
                    "class_id": _ccls,
                    "hp":       0, "hp_max": 0,
                    "level":    1, "effects": [],
                })
                session.known_eids.add(_seid)

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
            _live_eq = self.world_server.get_player_equipment_data(session.session_id)
            merged = self._build_save_merge(srv_data, session.last_client_payload, _live_eq)
            try:
                await save_character(session.char_data["id"], merged)
                saved += 1
            except Exception as e:
                log.error(f"[Session] ERRO autosave {session.username!r}: {e}")
        if saved:
            log.info(f"[Session] autosave  players={saved}  tick={self.world_server.tick_count}")

    async def _send_player_deaths(self, deltas: dict) -> None:
        for death in deltas.get("player_deaths", []):
            sid = death.get("session_id")
            if not sid:
                continue
            session = self._sessions.get(sid)
            if session:
                await session.send(MsgType.PLAYER_DEATH, {
                    "eid":        death["player_eid"],
                    "corpse_tx":  death.get("corpse_tx"),
                    "corpse_ty":  death.get("corpse_ty"),
                })

    async def _send_entity_deaths(self, deltas: dict) -> None:
        """Broadcast ENTITY_DEATH para sessões com a posição da morte no AOI.

        Só mortes de PLAYER passam por aqui (mobs usam died_eids no AOI_UPDATE);
        a entidade persiste como ghost, então o mapa resolve via MapLocation."""
        for ed in deltas.get("entity_deaths", []):
            _ed_map = self.world_server.get_entity_map(ed.get("eid", -1))
            for session in self._sessions_in_aoi(ed["tx"], ed["ty"], _ed_map):
                await session.send(MsgType.ENTITY_DEATH, ed)

    async def _send_player_revives(self, deltas: dict) -> None:
        for revive in deltas.get("player_revives", []):
            sid = revive.get("session_id")
            if not sid:
                continue
            session = self._sessions.get(sid)
            if session:
                await session.send(MsgType.PLAYER_REVIVE, {
                    "tx":       revive["tx"],
                    "ty":       revive["ty"],
                    "hp":       revive["hp"],
                    "hp_max":   revive["hp_max"],
                    "mana":     revive["mana"],
                    "max_mana": revive["max_mana"],
                })

    async def _send_ghost_state_updates(self, deltas: dict) -> None:
        for upd in deltas.get("ghost_states", []):
            sid = upd.get("session_id")
            if not sid:
                continue
            session = self._sessions.get(sid)
            if session:
                # Ghost mudou de mapa (morreu em zona não-principal): envia ZONE_CHANGE
                # antes do GHOST_STATE para o cliente carregar o mapa correto primeiro.
                zone_map = upd.get("zone_change_map")
                if zone_map and "tx" in upd and "ty" in upd:
                    await session.send(MsgType.ZONE_CHANGE, {
                        "map_file": zone_map,
                        "target_x": upd["tx"],
                        "target_y": upd["ty"],
                    })
                    session.known_eids.clear()

                payload = {
                    "is_ghost":        upd["is_ghost"],
                    "near_corpse":     upd["near_corpse"],
                    "graveyard_timer": upd["graveyard_timer"],
                }
                if "tx" in upd and "ty" in upd:
                    payload["tx"] = upd["tx"]
                    payload["ty"] = upd["ty"]
                await session.send(MsgType.GHOST_STATE, payload)

    # ── Broadcast helpers ─────────────────────────────────────────────────────

    def _sessions_in_aoi(self, tx: int, ty: int, map_file: "str | None",
                         origin_eid: int = -1,
                         exclude_sid: "str | None" = None) -> list:
        """ÚNICO filtro de destinatários para broadcast direto (fora do
        AOI_UPDATE): sessões autenticadas com (tx, ty) dentro do AOI.

        Antes cada loop de broadcast (SKILL_RESULT, SKILL_EFFECT, corpses,
        sons, mortes...) copiava o filtro de distância à mão — e NENHUM
        checava o MAPA: dois players em mapas diferentes com coordenadas
        próximas recebiam sons/skills/corpses um do outro (bug cross-map).

        - map_file: mapa do EVENTO (via WorldServer.get_entity_map/produtor).
          None = sem filtro de mapa (fallback p/ evento sem origem resolvível).
        - origin_eid >= 0: exige _can_see(receptor, origin_eid) — eventos de
          um caster invisível (Camuflagem) não vazam posição pra quem não o
          vê. O PRÓPRIO dono sempre passa (viewer == origin).
        - Métrica: Chebyshev via utils.in_aoi (canônica do projeto) — antes
          os broadcasts usavam círculo Euclidiano, divergindo do spawn
          inicial (WORLD_STATE) que já era Chebyshev.
        """
        result = []
        for s in list(self._sessions.values()):
            if not s.authenticated or s.session_id == exclude_sid:
                continue
            if (map_file is not None
                    and self.world_server.get_player_map(s.session_id) != map_file):
                continue
            sx, sy = self.world_server.get_tile_pos(s.session_id)
            if not _in_aoi(tx, ty, sx, sy, AOI_RADIUS):
                continue
            if (origin_eid >= 0 and s.entity_id != origin_eid
                    and not _can_see(self.world_server.world, s.entity_id, origin_eid)):
                continue
            result.append(s)
        return result

    async def _broadcast_all(self, msg_type: MsgType, payload: dict) -> None:
        tasks = [s.send(msg_type, payload)
                 for s in self._sessions.values() if s.authenticated]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_aoi_from_session(self, origin: Session,
                                          msg_type: MsgType, payload: dict) -> None:
        ox, oy = self.world_server.get_tile_pos(origin.session_id)
        _map = self.world_server.get_player_map(origin.session_id)
        tasks = [s.send(msg_type, payload)
                 for s in self._sessions_in_aoi(ox, oy, _map,
                                                origin_eid=origin.entity_id)]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_aoi_except(self, origin: Session,
                                    msg_type: MsgType, payload: dict) -> None:
        ox, oy = self.world_server.get_tile_pos(origin.session_id)
        _map = self.world_server.get_player_map(origin.session_id)
        tasks = [s.send(msg_type, payload)
                 for s in self._sessions_in_aoi(ox, oy, _map,
                                                origin_eid=origin.entity_id,
                                                exclude_sid=origin.session_id)]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
