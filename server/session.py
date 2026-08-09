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
from shared.constants import (AOI_RADIUS, AOI_EXIT_BUFFER, PROTOCOL_VERSION, TICK_RATE,
                               ALLY_VISION_RADIUS_PLAYER, ALLY_VISION_RADIUS_MINION)
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
        self.is_gm         = False  # preenchido no login (server/auth.py::is_gm da conta)
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
        # Visão compartilhada de time (instanciado — arena/battlefield/dungeon).
        # Recomputado 1x/tick em _dispatch_tick_deltas, ANTES de qualquer AOI.
        # player_eid -> [(tx, ty, radius), ...] de cada ALIADO (outro player,
        # torre ou minion com a mesma Faction + mesma instância). Vazio pra
        # quem não está num contexto de time (custo ~zero, caso comum).
        self._ally_vision_centers: dict[int, list[tuple[int, int, int]]] = {}
        self.world_server.register_on_tick(self._on_tick)

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    async def on_connect(self, ws, session_id: str) -> Session:
        session = Session(ws, session_id)
        self._sessions[session_id] = session
        log.info(f"[Session] +connect {session_id}  total={len(self._sessions)}")
        return session

    async def _persist_character(self, session: "Session", context: str = "",
                                 patch_fn=None) -> "dict | None":
        """Único ponto de persistência real (DB) de personagem — todo
        caminho que grava no banco (SAVE_STATE, TALENT_UPDATE, entrega de
        quest, autosave periódico, disconnect) DEVE passar por aqui em vez
        de montar get_player_save_data/_build_save_merge/save_character
        manualmente. Retorna o dict `merged` persistido, ou None se pulou
        (sem sessão válida, sem srv_data, ou dentro da instância).

        INCIDENTE REAL (02/08/2026, relatado pelo usuário — personagem
        "totalmente desconfigurado", talentos/skills reais sumidos):
        `is_in_normalized_progression` (server/instance_progression.py) —
        NUNCA persiste enquanto o player está dentro de uma instância de
        progressão normalizada (battleground de teste). O overlay da
        instância (level 1, talentos maxados/skills parciais/inventário de
        6 slots/gold=INSTANCE_STARTING_GOLD) é 100% temporário/descartável
        por design (decisão #4, `exit_normalized_progression` restaura o
        real) — mas ANTES desta correção só o disconnect verificava isso
        (`on_disconnect` chama `exit_normalized_progression` antes de
        salvar). Os outros 4 pontos de save espalhados pelo código NUNCA
        checavam: SAVE_STATE automático do cliente (disparado a cada troca
        de mapa E logo após qualquer inv_snapshot/equip_snapshot de
        STATS_UPDATE — ou seja, ao ENTRAR ou SAIR do battleground),
        TALENT_UPDATE, entrega de quest, e o autosave de 5 em 5 minutos —
        qualquer um bastava pra sobrescrever o personagem real no banco
        com o piso de nível 1 da instância. Mesma classe de bug do
        incidente de disconnect já documentado em §34.74.1/§34.74.2, só
        que sem exigir crash/disconnect — bastava ficar tempo normal
        dentro da instância. Ver ARQUITETURA_ONLINE.md §34.74.15.

        `patch_fn` (opcional) — chamado com o dict `merged` logo antes de
        persistir, pra caller que precisa sobrescrever 1-2 campos com
        dado ao vivo mais recente do que `session.last_client_payload`
        (ex.: kill-XP em world_server.py, que sobrescreve talentos com o
        TalentTree ATUAL do ECS — o cliente ainda não recebeu a
        notificação de level-up nesse exato instante, então o cache
        ficaria com available_points desatualizado). Muta `merged` in
        place, sem retorno."""
        if not session.authenticated or not session.char_data.get("id"):
            return None
        from server.instance_progression import is_in_normalized_progression as _is_norm_persist
        if _is_norm_persist(self.world_server, session.entity_id):
            return None
        from server.auth import save_character
        srv_data = self.world_server.get_player_save_data(session.session_id)
        if not srv_data:
            return None
        _live_eq = self.world_server.get_player_equipment_data(session.session_id)
        _live_hotbar = self.world_server.get_player_hotbar_data(session.session_id)
        _live_talents = self.world_server.get_player_talent_data(session.session_id)
        _live_inv = self.world_server.get_player_inventory_data(session.session_id)
        merged = self._build_save_merge(srv_data, session.last_client_payload,
                                        _live_eq, _live_hotbar, _live_talents,
                                        _live_inv)
        if patch_fn is not None:
            patch_fn(merged)
        try:
            await save_character(session.char_data["id"], merged)
            return merged
        except Exception as e:
            log.error(f"[Session] ERRO ao persistir {session.username!r} ({context}): {e}")
            return None

    @staticmethod
    def _build_save_merge(srv_data: dict, client_payload: dict,
                          live_equipment: dict | None = None,
                          live_hotbar: list | None = None,
                          live_talents: dict | None = None,
                          live_inventory: "list | None" = None) -> dict:
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
        - inventory: SERVIDOR autoritativo quando `live_inventory` é
          fornecido — Fase 0 do roteiro de saneamento (07/08/2026, ver
          PROBLEMAS_ARQUITETURA.md §12/§13), QUARTA ocorrência do mesmo
          padrão de bug (equipamento/hotbar/talentos acima): `inventory`
          nunca teve fallback ao vivo, só `client_p.get("inventory")` —
          o mesmo overlay de instância que contamina equipment/talents
          também troca o `Inventory` real por um de 6 slots, e nenhum
          handler (`_handle_save_state`/`_handle_inventory_update`) tinha
          guard contra cachear isso. `live_inventory`
          (WorldServer.get_player_inventory_data, lido do Inventory ao
          vivo — sempre o real, mesma garantia de `live_equipment`) usa
          `is not None` em vez de truthy: uma bag genuinamente vazia
          (`[]`) é um estado real válido, não "indisponível" — tratar
          `[]` como falsy cairia de novo no cache stale exatamente pro
          caso de um personagem sem itens.
        - talents: SERVIDOR autoritativo quando `live_talents` é
          fornecido — TERCEIRA ocorrência do mesmo padrão de bug nesta
          sessão (equipamento acima, skills["hotbar"] abaixo): bug real
          (06/08/2026) — `enter_normalized_progression`
          (instance_progression.py) troca o TalentTree por um overlay
          com TODO talento do build no MÁXIMO (decisão de design da
          instância de progressão normalizada); qualquer TALENT_UPDATE
          OU SAVE_STATE mandado enquanto o player está DENTRO da
          instância cacheia esse overlay maxado em
          `client_payload["talents"]` (diferente de hotbar,
          `_collect_save_state` NÃO omite "talents" de propósito) — nada
          limpa esse cache quando `exit_normalized_progression` restaura
          o TalentTree real, então o próximo save persistia os talentos
          maxados no banco (personagem saía da BG com todos os pontos
          aplicados). `live_talents` (WorldServer.get_player_talent_data,
          lido do TalentTree ao vivo na hora do save — sempre o real,
          já que `_persist_character` recusa persistir dentro da
          instância) elimina a dependência desse cache. Ver
          ARQUITETURA_ONLINE.md §34.74.49.
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
        - skills["learned"]: SERVIDOR autoritativo (srv_data, sempre
          ao vivo) — Fase 0 do roteiro de saneamento (07/08/2026, ver
          PROBLEMAS_ARQUITETURA.md §12/§13): antes o cache do cliente
          vencia quando truthy, o MESMO subcampo que ficou de fora
          quando `hotbar` (abaixo) foi corrigido — vetor real:
          desconectar dentro da instância restaura o `PlayerSkills`
          real no ECS (`exit_normalized_progression`), mas
          `_persist_character` roda em seguida sem passar por
          `_handle_save_state`/`sync_player_skills` — o cache antigo
          (possivelmente contaminado pelo overlay da instância) nunca
          era corrigido antes do save. `srv_data.get("skills")` já é
          sempre ao vivo (`get_player_save_data` lê `ps.learned_skill_ids`
          na hora) — fora da instância, `sync_player_skills` já aplicou
          o que o cliente reportou ANTES de `_persist_character` rodar
          (mesmo handler, `_handle_save_state`), então preferir srv_data
          nunca perde uma skill aprendida de verdade. `client_p["skills"]`
          só sobra como fallback se srv_data não tiver o campo (sem
          PlayerSkills, caso degenerado).
        - skills["hotbar"]: SERVIDOR autoritativo quando `live_hotbar` é
          fornecido — MESMA classe de bug do equipamento acima, mesmo fix:
          `client_payload["skills"]` nunca tinha "hotbar" (o cliente manda
          de propósito só "learned", ver client/save_sync_handlers.py::
          _collect_save_state — layout é tratado como "UI local"), e
          `_handle_hotbar_update` só atualiza o CACHE da sessão, nunca
          persiste sozinho — qualquer SAVE_STATE subsequente (inclusive um
          disparado sem o usuário perceber, ex.: ao entrar/sair de
          battleground, ver instance_progression.py) reconstruía
          `session.last_client_payload` inteiro a partir de um payload sem
          "hotbar", apagando o que `_handle_hotbar_update` tinha posto lá
          — bug real: hotbar embaralhada só no PRÓXIMO login (a hotbar AO
          VIVO nunca quebrava, só o valor gravado no banco). `live_hotbar`
          (WorldServer.get_player_hotbar_data, lido do PlayerSkills ao
          vivo na hora do save) elimina a dependência desse cache pra esse
          subcampo. Ver ARQUITETURA_ONLINE.md §34.74.47.
        - quests: SEMPRE servidor (mesma regra de skill_levels — progresso/entrega de
          quest é server-autoritativo, ver quest_logic.py/PROBLEMAS_ARQUITETURA.md)
        - stats base (level, xp, attrs): servidor
        """
        client_p  = client_payload
        srv_stats = srv_data.get("stats", {})
        # gold e max_hp já vêm corretos de get_player_save_data (Wallet/CombatStats vivos) —
        # dict(srv_stats) preserva os dois sem precisar (e sem dever) olhar o payload do cliente.
        merged_stats = dict(srv_stats)
        # Fase 0 (07/08/2026) — srv_data["skills"] (sempre ao vivo, ver
        # docstring acima) vence por padrão; client_p só serve de
        # fallback pro caso degenerado de srv_data não ter o campo.
        _cs_raw = client_p.get("skills") if client_p else None
        client_skills = _cs_raw if (_cs_raw and _cs_raw.get("learned")) else None
        _srv_skills = srv_data.get("skills") or None
        _final_skills = _srv_skills if _srv_skills else client_skills
        # Fase 7 (06/08/2026) — "hotbar" sempre do ECS ao vivo quando
        # disponível, nunca do cache do cliente (ver docstring acima e
        # ARQUITETURA_ONLINE.md §34.74.47). Incondicional quando presente,
        # mesmo espírito de `live_equipment` abaixo.
        if live_hotbar:
            _final_skills = dict(_final_skills) if _final_skills else {}
            _final_skills["hotbar"] = live_hotbar

        # Fog: union de tiles explorados (servidor DB + cliente atual).
        # O cliente sempre envia o conjunto completo (recebeu o fog do servidor
        # no LOGIN_OK e acumulou novas descobertas), portanto a autoridade é
        # do cliente — mas fazemos union para segurança em caso de múltiplos logins.
        # Bitmap comprimido (shared/fog_codec.py) — decode_fog aceita formato
        # novo E antigo (coordenada crua), encode_fog sempre grava no novo (ver
        # PROBLEMAS_ARQUITETURA.md, bug real 19/07/2026 — fog_json passava de
        # 1 MB numa conta bem explorada).
        import json as _json
        from shared.fog_codec import encode_fog as _encode_fog_sv, decode_fog as _decode_fog_sv
        _srv_fog_raw = srv_data.get("fog_json", "{}")
        try:
            _srv_fog_data = _json.loads(_srv_fog_raw) if isinstance(_srv_fog_raw, str) else (_srv_fog_raw or {})
        except Exception:
            _srv_fog_data = {}
        _srv_fog = _decode_fog_sv(_srv_fog_data)
        _cli_fog = _decode_fog_sv((client_p.get("fog") or {}) if client_p else {})
        if _cli_fog:
            merged_sets: dict = dict(_srv_fog)
            for _mk, _cli_set in _cli_fog.items():
                _mk = _mk.replace("\\", "/")
                merged_sets[_mk] = merged_sets.get(_mk, set()) | _cli_set
            merged_fog = _encode_fog_sv(merged_sets)
        else:
            merged_fog = _encode_fog_sv(_srv_fog)

        return {
            "tile_x":    srv_data.get("tile_x", 10),
            "tile_y":    srv_data.get("tile_y", 10),
            "hp":        min(srv_data.get("hp", 100), merged_stats.get("max_hp", 9999)),
            "mp":        srv_data.get("mp", 100),
            "stats":     merged_stats,
            "inventory": live_inventory if live_inventory is not None else (client_p.get("inventory") if client_p else None),
            "equipment": live_equipment if live_equipment else (client_p.get("equipment") if client_p else None),
            "talents":   live_talents if live_talents else (client_p.get("talents") if client_p else None),
            "skills":    _final_skills,
            "fog":       merged_fog if merged_fog else None,
            # skill_levels: SEMPRE servidor — progressão por uso é
            # server-autoritativa, cliente nunca influencia (ver
            # stats_system.grant_skill_xp / PROBLEMAS_ARQUITETURA.md).
            "skill_levels": srv_data.get("skill_levels"),
            # quests: SEMPRE servidor — progresso/entrega é server-autoritativa,
            # mesma regra de skill_levels (ver quest_logic.py/PROBLEMAS_ARQUITETURA.md).
            "quests": srv_data.get("quests"),
            # char_stats: SEMPRE servidor — estatísticas acumuladas pro modal
            # de estatísticas (Fase E), mesma regra de quests/skill_levels.
            "char_stats": srv_data.get("char_stats"),
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
            # Arena: desconectar em partida ativa conta como eliminação e já
            # restaura mapa/tile de origem (MatchProcessorMixin.end_matches_of)
            # — tem que rodar ANTES do save logo abaixo. Sem isso, o save
            # capturava o map_id sintético da instância da arena (só existe em
            # memória) + o tile relativo ao spawn da arena, persistindo os
            # dois no banco — próximo login caía no mapa principal só que com
            # o tile da arena (bug real relatado pelo usuário 20/07/2026).
            self.world_server.end_matches_of(session.entity_id)
            # Fila REAL de BG (04/08/2026): mesmo motivo do end_matches_of
            # acima — sai da progressão normalizada e restaura Faction/
            # posição ANTES do save (server/bg_queue_processor.py::
            # end_bg_matches_of → request_bg_leave já chama
            # exit_normalized_progression sozinho).
            self.world_server.end_bg_matches_of(session.entity_id)
            # Battleground de teste (debug): mesmo motivo do end_matches_of
            # acima — sai da progressão normalizada (restaura level/talento/
            # skill/gold/itens reais) ANTES do save, senão o disconnect
            # persiste o estado NORMALIZADO (ex.: level 1) como se fosse o
            # personagem real. Incidente real, 01/08/2026 (level 23 perdido
            # sem esse hook) — ver server/debug_battleground.py::on_disconnect,
            # ARQUITETURA_ONLINE.md §34.74.1. Defesa em profundidade: mesmo
            # que um futuro processador de Battlefield esqueça de tratar
            # disconnect, este check genérico ainda restaura o estado real.
            from server import debug_battleground as _dbg_bg
            _dbg_bg.on_disconnect(self.world_server, session.entity_id)
            from server.instance_progression import (
                is_in_normalized_progression as _is_norm_prog,
                exit_normalized_progression as _exit_norm_prog,
            )
            if _is_norm_prog(self.world_server, session.entity_id):
                _exit_norm_prog(self.world_server, session.entity_id)
            # Salva ANTES de remover a entidade do ECS
            merged = await self._persist_character(session, context="disconnect")
            if merged:
                log.info(f"[Session] saved {session.username!r}  "
                      f"tile=({merged['tile_x']},{merged['tile_y']})  "
                      f"hp={merged['hp']}  gold={merged['stats'].get('gold', 0)}")
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
            # Arena já foi encerrada mais acima (antes do save) — ver comentário lá.
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
        session.is_gm         = bool(char_data.get("is_gm", False))
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
        # Diagnóstico do rollback do Interceptar (06/08/2026, ver
        # ARQUITETURA_ONLINE.md §34.74.50) — MOVE normal processado no
        # mesmo tick/janela que um CAST_SKILL de dash é candidato a
        # colidir com a correção de posição do dash (ver comentário em
        # skill_processor.py sobre "corrida entre MOVE e CAST_SKILL").
        # Só grava quando RPG_DEBUG_INTERCEPTAR está ligado (no-op
        # normalmente).
        from debug.interceptar_debug import INTERCEPTAR_DBG as _IDBG_mv
        _IDBG_mv.log("MOVE_REQUEST", tick=self.world_server.tick_count,
                     player_eid=session.entity_id, tx=tx, ty=ty, accepted=accepted)
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

    # ── GM (menu de debug F12) — só `session.is_gm` tem efeito ────────────
    # Mesmo padrão de bypass negado silenciosamente já usado em
    # skill_processor.py pra taunt/stun: sem is_gm, a mensagem é ignorada,
    # sem resposta e sem log de erro — não confirma nem nega pro cliente
    # (não dá pinga de informação a uma tentativa não-autorizada).

    async def _handle_gm_levelup(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated or not session.is_gm:
            return
        eid = session.entity_id
        levels = max(1, min(200, int(payload.get("levels", 1))))
        from engine.components import CharacterStats, CombatStats, PermanentStats, TalentTree
        from engine.stats_system import process_levelups
        char = self.world_server.world.get_component(eid, CharacterStats)
        cs   = self.world_server.world.get_component(eid, CombatStats)
        perm = self.world_server.world.get_component(eid, PermanentStats)
        if not char or not cs:
            return
        for _ in range(levels):
            char.current_xp = char.xp_to_next_level
            process_levelups(self.world_server.world, eid, char, cs, perm)
        cs.current_hp = cs.max_hp
        tt = self.world_server.world.get_component(eid, TalentTree)
        self.world_server.queue_stats_update({
            "player_eid":    eid,
            "xp":            char.current_xp,
            "level":         char.level,
            "hp":            cs.current_hp,
            "hp_max":        cs.max_hp,
            "talent_points": tt.available_points if tt else 0,
        })
        log.info(f"[GM] {session.username!r} usou GM_LEVELUP levels={levels} "
                 f"-> nivel {char.level}")

    async def _handle_gm_add_gold(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated or not session.is_gm:
            return
        eid = session.entity_id
        amount = max(0, min(1_000_000, int(payload.get("amount", 0))))
        if amount == 0:
            return
        from engine.components import Wallet
        wallet = self.world_server.world.get_component(eid, Wallet)
        if not wallet:
            return
        wallet.gold += amount
        self.world_server.queue_stats_update({"player_eid": eid, "gold": wallet.gold})
        log.info(f"[GM] {session.username!r} usou GM_ADD_GOLD amount={amount} "
                 f"-> gold={wallet.gold}")

    async def _handle_gm_add_item(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated or not session.is_gm:
            return
        eid = session.entity_id
        item_name = str(payload.get("item_name", ""))
        if not item_name:
            return
        from engine.components import Inventory
        inv = self.world_server.world.get_component(eid, Inventory)
        if not inv:
            return
        # Mesmo catálogo que client/debug_handlers.py::_get_debug_item_catalog
        # usa pra listar itens no F12 (content.loot_tables._T + SHOPS),
        # casado por nome de exibição — não é um id estável, mas é o mesmo
        # contrato que o resto do menu de debug já usa.
        from content.loot_tables import _T
        from content.merchant_data import SHOPS
        factory = None
        for f in _T.values():
            if f().name == item_name:
                factory = f
                break
        if factory is None:
            for shop in SHOPS.values():
                for entry in shop["stock"]:
                    if entry["factory"]().name == item_name:
                        factory = entry["factory"]
                        break
                if factory:
                    break
        if factory is None:
            return
        if len(inv.items) >= inv.max_slots:
            return
        item = factory()
        inv.items.append(item)
        from server.server_death_handler import _serialize_item
        await session.send(MsgType.INVENTORY_UPDATE, {
            "items": [_serialize_item(item)], "removed": [],
        })
        log.info(f"[GM] {session.username!r} usou GM_ADD_ITEM item_name={item_name!r}")

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
        """Recebe estado completo do cliente e persiste no banco (via
        `_persist_character` — no-op enquanto o player está dentro de uma
        instância de progressão normalizada, ver docstring lá)."""
        if not session.authenticated or not session.char_data.get("id"):
            return
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
        await self._persist_character(session, context="save_state")

    async def _handle_chat(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        text    = str(payload.get("text", ""))[:200]
        channel = payload.get("channel", "local")

        # Gancho de debug (01/08/2026) — testa mapa/rotas do futuro modo
        # Battlefield sem fila/matchmaking. Ver server/debug_battleground.py.
        # Intercepta ANTES de virar chat de verdade; resposta só pro remetente.
        if text.strip().lower().startswith("/testbg"):
            from server import debug_battleground
            args = text.strip().split()[1:]
            reply, zone_change, countdown = debug_battleground.handle_command(
                self.world_server, session, session.entity_id, args)
            if zone_change:
                # Flush do STATS_UPDATE (Inventory/Equipment/CharacterStats
                # reais restaurados por enter_/exit_normalized_progression)
                # ANTES do ZONE_CHANGE — ver docstring de _flush_stats_updates.
                # zone_change aqui é enviado IMEDIATAMENTE (fora do dispatch
                # batched por-tick), então sem este flush antecipado o
                # cliente processava a troca de mapa (autosave no fim de
                # _do_transition) com o Inventory ainda "de instância".
                await self._flush_stats_updates()
                await session.send(MsgType.ZONE_CHANGE, zone_change)
                session.known_eids.clear()
            if countdown is not None:
                # Reaproveita o overlay cosmético de contagem da Arena
                # (client/arena_handlers.py::_draw_arena_countdown_overlay)
                # — só depende de _arena_countdown_deadline_val, sem gate
                # de "está numa partida de arena". Pedido do usuário,
                # 01/08/2026: sem isso não dá pra saber quando o portão
                # abre pra ir explorar o mapa. `countdown` já vem no
                # formato exato do payload ({"remaining","my_faction"} —
                # ver debug_battleground.handle_command).
                await session.send(MsgType.ARENA_COUNTDOWN, countdown)
            await session.send(MsgType.CHAT_MESSAGE, {
                "sender": "Sistema", "eid": -1, "text": reply,
                "channel": "local", "color": [255, 200, 80],
            })
            return
        # "eid" além de "sender": nome de personagem NÃO é único (duplicatas
        # legadas, ver PROBLEMAS_ARQUITETURA.md) — sem o eid, o cliente só
        # consegue posicionar o balão de fala comparando nomes, o que
        # acertava o personagem ERRADO quando dois compartilhavam nome (bug
        # real relatado pelo usuário, mesma classe do resultado de arena
        # invertido).
        msg = {"sender": session.display_name, "eid": session.entity_id,
               "text": text, "channel": channel, "color": [220, 210, 150]}
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
        # Atualiza cache e persiste só os talentos no DB (via
        # _persist_character — no-op enquanto o player está dentro de uma
        # instância de progressão normalizada, ver docstring lá).
        if session.last_client_payload is None:
            session.last_client_payload = {}
        session.last_client_payload["talents"] = _checked
        await self._persist_character(session, context="talent_update")

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

        # Recompensa de item com escolha (23/07/2026, pedido do usuário):
        # valida ANTES de completar a quest — se o cliente mandar um
        # chosen_item que não está no pool real (cliente adulterado/
        # dessincronizado), recusa a entrega inteira (mesmo padrão de
        # can_turn_in acima), nunca conceder um item fora do catálogo da
        # quest silenciosamente.
        from content.quests_data import QUESTS as _QUESTS_qt
        qdef = _QUESTS_qt.get(qid)
        if qdef is None:
            return
        chosen_item = str(payload.get("chosen_item", ""))
        chosen_entry = None
        if qdef.reward.choice:
            for entry in qdef.reward.choice:
                _key, _stack = quest_logic.normalize_reward_entry(entry)
                if _key == chosen_item:
                    chosen_entry = (_key, _stack)
                    break
            if chosen_entry is None:
                log.warning(f"[QuestTurnIn] chosen_item invalido '{chosen_item}' "
                           f"pra quest '{qid}' (player={eid}) — recusado")
                return

        reward, consumed_items = quest_logic.complete_quest(self.world_server.world, eid, ql, qid)
        if reward is None:
            return

        if reward.xp > 0:
            # XP de instância (02/08/2026) usa curva/mecânica PRÓPRIA (level
            # cap 15, +3 pontos de talento por level) — mesmo desvio já
            # aplicado em world_server.py pro XP de kill de mob/minion/torre
            # (consume_xp()). Gap real achado nesta sessão: recompensa de
            # XP de QUEST ainda caía direto no process_levelups REAL, sem
            # checar is_in_normalized_progression — sem esse desvio, o
            # process_levelups real usaria a curva ERRADA e o save-to-DB
            # (nenhum aqui, mas process_levelups mexe em char/cs "de
            # verdade") escreveria estado da instância como se fosse o
            # personagem real.
            from server.instance_progression import (
                is_in_normalized_progression as _is_norm_quest,
                grant_instance_xp as _grant_ixp_quest,
            )
            if _is_norm_quest(self.world_server, eid):
                _grant_ixp_quest(self.world_server, eid, reward.xp)
            else:
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

        # Itens de recompensa (23/07/2026, pedido do usuário) — fixos
        # (reward.items, sempre) + o escolhido (chosen_entry, já validado
        # acima). Servidor não toca no Inventory ECS aqui — mesmo padrão
        # de LOOT_RESULT (server/loot_processor.py::request_loot): o
        # cliente é quem materializa o item na bag local a partir do
        # payload, e o INV_SYNC periódico do cliente mantém o mirror do
        # servidor atualizado depois.
        reward_entries = list(qdef.reward.items) + ([chosen_entry] if chosen_entry else [])
        granted_dicts = []
        if reward_entries:
            from server.server_death_handler import _serialize_item
            for entry in reward_entries:
                item_key, stack = quest_logic.normalize_reward_entry(entry)
                factory = quest_logic.resolve_reward_item_factory(item_key)
                if factory is None:
                    log.warning(f"[QuestTurnIn] item_key '{item_key}' da quest '{qid}' "
                               f"não existe em nenhum catálogo — ignorado")
                    continue
                item = factory()
                item.stack = max(1, min(stack, item.max_stack))
                granted_dicts.append(_serialize_item(item))

        # consumed_items (28/07/2026, pedido do usuário — bug real: item de
        # quest entregue continuava "fantasma" na bag) — itens que
        # complete_quest() removeu do Inventory DESTE SERVIDOR pra objetivos
        # collect_item. O servidor nunca tocou na bag do CLIENTE (mesmo
        # racional client-authoritative de request_loot) — sem mandar essa
        # lista, o cliente nunca saberia que precisa tirar o item também.
        # Reaproveita a MESMA mensagem de itens concedidos (campo novo
        # "removed") em vez de criar um MsgType novo.
        if granted_dicts or consumed_items:
            await session.send(MsgType.INVENTORY_UPDATE, {
                "items": granted_dicts, "removed": consumed_items,
            })

        # Skill de recompensa (25/07/2026, pedido do usuário) — diferente de
        # item: o servidor grava em PlayerSkills.learned_skill_ids NA HORA
        # (skill tem gate de autorização server-side, is_skill_authorized(),
        # que olha o learned_skill_ids do PRÓPRIO servidor — precisa ser
        # real imediatamente, não só depender do cliente sincronizar
        # depois, diferente do padrão de item acima). Classe errada é
        # ignorada com warning, mesmo espírito de item_key inválido — não
        # derruba o resto da entrega.
        if qdef.reward.skill:
            from content.skill_config import SKILL_CATALOG as _SC_qt
            from engine.components import PlayerSkills as _PS_qt, CharacterStats as _CSchar_qt
            _skill_id = qdef.reward.skill
            _entry = _SC_qt.get(_skill_id)
            _char_qt = self.world_server.world.get_component(eid, _CSchar_qt)
            if _entry is None:
                log.warning(f"[QuestTurnIn] skill '{_skill_id}' da quest '{qid}' "
                           f"não existe em SKILL_CATALOG — ignorada")
            elif _entry.get("class_id") and (not _char_qt or _char_qt.class_id != _entry["class_id"]):
                log.warning(f"[QuestTurnIn] skill '{_skill_id}' da quest '{qid}' "
                           f"é de outra classe ({_entry.get('class_id')}) — ignorada "
                           f"(player={eid}, class={_char_qt.class_id if _char_qt else '?'})")
            else:
                _ps_qt = self.world_server.world.get_component(eid, _PS_qt)
                if _ps_qt is not None and _skill_id not in _ps_qt.learned_skill_ids:
                    _ps_qt.learned_skill_ids.add(_skill_id)
                    _new_sk = _PS_qt._make_skill(_skill_id, _SC_qt)
                    if _new_sk is not None:
                        try:
                            _idx = _ps_qt.skills.index(None)
                            _ps_qt.skills[_idx] = _new_sk
                        except ValueError:
                            _ps_qt.skills.append(_new_sk)
                    await session.send(MsgType.SKILL_GRANTED,
                                       {"skill_id": _skill_id, "name": _entry.get("name", _skill_id)})

        # Persiste imediatamente (mesmo padrão de TALENT_UPDATE) — crash do
        # servidor não perde a entrega que já concedeu XP/gold/itens. Via
        # _persist_character — no-op enquanto o player está dentro de uma
        # instância de progressão normalizada, ver docstring lá.
        await self._persist_character(session, context="quest_turn_in")

        await session.send(MsgType.QUEST_UPDATE, {
            "active":        {q: list(p) for q, p in ql.active.items()},
            "completed":     list(ql.completed),
            "completed_qid": qid,
        })

    async def _handle_hotbar_update(self, session: Session, payload: dict, ts: int) -> None:
        """Atualiza cache da barra de ações (persistido no próximo save
        completo) E o `PlayerSkills` AO VIVO do ECS (05/08/2026, bug real
        relatado pelo usuário: reordenar a hotbar pouco antes de entrar na
        BG não tinha efeito nenhum — `enter_normalized_progression`
        consultava o `PlayerSkills` ao vivo pra achar o slot real de cada
        skill, mas esse componente só era carregado do banco no login e
        NUNCA era tocado por este handler, só o cache de save. Resultado:
        qualquer edição de hotbar feita DURANTE a sessão (sem relogar)
        ficava invisível pro resto do servidor até o próximo save/login —
        inclusive pro snapshot que `exit_normalized_progression` restaura
        ao sair da BG, que devolvia esse layout desatualizado por cima do
        real). Único ponto de verdade agora: aplica no componente ao vivo
        (reordena — NUNCA concede skill nova por aqui, só
        `learned_skill_ids` já existente; sem essa checagem um cliente
        malicioso podia mandar um skill_id que não tem aprendido e ganhar
        a skill de graça)."""
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

            eid = self.world_server._player_eids.get(session.session_id)
            if eid is not None:
                from engine.components import PlayerSkills as _PSHbu
                from content.skill_config import SKILL_CATALOG as _SCHbu
                ps = self.world_server.world.get_component(eid, _PSHbu)
                if ps is not None:
                    for i, sid in enumerate(skills):
                        if i >= len(ps.skills):
                            break
                        if sid and sid not in ps.learned_skill_ids:
                            continue  # nunca concede skill nova por este canal
                        cur = ps.skills[i]
                        if sid and cur is not None and cur.skill_id == sid:
                            continue  # slot não mudou — preserva estado ao vivo
                        ps.skills[i] = _PSHbu._make_skill(sid, _SCHbu) if sid else None
        # Consumíveis são UI local — só precisam estar no próximo SAVE_STATE

    async def _handle_loot_request(self, session: Session, payload: dict, ts: int) -> None:
        """
        Player clicou num corpo para sacar (granular — `take`: "gold"/
        "item"/"all", ver WorldServer.request_loot).
        Se for o dono/grupo → retorna itens via LOOT_RESULT.
        Se não → ignora silenciosamente (regra de negócio).
        """
        if not session.authenticated:
            return
        corpse_id = int(payload.get("corpse_id", -1))
        if corpse_id < 0:
            return
        take      = payload.get("take", "all")
        item_name = payload.get("item_name", "")
        loot = self.world_server.request_loot(session.session_id, corpse_id, take, item_name)
        if loot is not None:
            await session.send(MsgType.LOOT_RESULT, {
                "corpse_id": corpse_id,
                "items":     loot["items"],
                "coins":     loot["coins"],
            })
            # Avisa o RESTO do grupo (todos que também receberam este
            # corpse via LOOT_AVAILABLE, exceto quem acabou de sacar) que
            # algo saiu — sem isso cada um só descobre a mudança quando
            # CLICA algo próprio, e até lá fica vendo ouro/item "fantasma"
            # já pego por outro membro (bug real relatado pelo usuário
            # 17/07/2026: clicar no fantasma voltava vazio e o corpo
            # sumia sem nunca ter dado nada — a cópia local nunca era
            # corrigida). Cliente só sincroniza a cópia local, nunca
            # credita (ver ui/systems.py::LootSystem / client/
            # network_handlers.py::_handle_msg_loot_update).
            if loot["coins"] > 0 or loot["items"]:
                _requester_eid = self.world_server._player_eids.get(session.session_id)
                _loot_owner_eid = self.world_server._corpses.get(corpse_id, {}).get("owner_eid", -1)
                _party_eids = self.world_server.get_party_members(_loot_owner_eid)
                _update_payload = {
                    "corpse_id":        corpse_id,
                    "coins_taken":      loot["coins"],
                    "item_names_taken": [it.get("name", "") for it in loot["items"]],
                }
                for _p_eid in _party_eids:
                    if _p_eid == _requester_eid:
                        continue
                    _p_sid = self.world_server.get_session_id_for_player(_p_eid)
                    _p_sess = self._sessions.get(_p_sid) if _p_sid else None
                    if _p_sess and _p_sess.authenticated:
                        await _p_sess.send(MsgType.LOOT_UPDATE, _update_payload)
            # Corpo só some pra AOI quando fica REALMENTE vazio — sacar só
            # o ouro (ou só um item) não deveria remover o resto do loot
            # da visão do resto do grupo (bug real relatado pelo usuário
            # 17/07/2026: corpo sumia com itens ainda dentro).
            #
            # Harvestable (no_decay=True) é PERMANENTE e tem seu próprio
            # mecanismo de "fica visível vazio até reabastecer" (Fase M2,
            # consume_harvestable_refills) — nunca deveria disparar ESTE
            # despawn genérico (bug real relatado pelo usuário 25/07/2026:
            # esvaziar a caixa a removia da tela de TODO MUNDO no AOI,
            # inclusive quem nem tinha saqueado; ao reabastecer depois, o
            # cliente não achava mais o local_eid em _available_loot e
            # caía no fallback antigo de create_corpse, desenhando a
            # elipse velha por cima do que deveria ser a caixa de novo).
            #
            # quest_rolls (Fase L1, resolução condicional por jogador —
            # server/loot_processor.py::_resolve_conditional_loot_for) é
            # um pote SEPARADO do comum ("items") — nunca entrava nesta
            # checagem (bug real relatado pelo usuário 28/07/2026: matar
            # aranha com "Veneno Mortal" ativa + Reciclagem do arqueiro
            # dando flecha no loot comum — sacar só a flecha esvaziava
            # "items", o corpo era declarado vazio e sumia da AOI de
            # todo mundo com o Veneno de Aranha ainda intocado no pote
            # pessoal, nunca mais lootável). Precisa checar se ALGUM
            # jogador ainda tem item pessoal pendente antes de declarar
            # o corpo realmente vazio.
            corpse_data = self.world_server._corpses.get(corpse_id, {})
            _still_has_loot = (bool(corpse_data.get("items"))
                              or corpse_data.get("coins", 0) > 0
                              or any(corpse_data.get("quest_rolls", {}).values()))
            if not _still_has_loot and not corpse_data.get("no_decay"):
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

        # Se o player está num mapa não-principal (ex: caverna), troca pro
        # mapa principal ANTES do snap — sem isso o tile_x/tile_y virava o
        # spawn de map_1, mas o mapa carregado continuava sendo o antigo
        # (bug real relatado pelo usuário 22/07/2026: personagem ficava
        # preso na caverna, só com a coordenada errada). Mesmo padrão de
        # respawn_system.py::_handle_release_spirit — nunca faz isso dentro
        # de uma partida de Arena (yankaria o player pra fora da instância;
        # só _arena_leave_now deve mexer no mapa/posição de quem está numa
        # partida).
        _in_arena_match_us = player_eid in self.world_server._player_match_id
        from engine.components import MapLocation as _MLus
        _ml_us = self.world_server.world.get_component(player_eid, _MLus)
        current_map_us = _ml_us.map_file if _ml_us else self.world_server._map_file
        if current_map_us != self.world_server._map_file and not _in_arena_match_us:
            self.world_server.transfer_player(sid, player_eid, self.world_server._map_file, rx, ry)
            await session.send(MsgType.ZONE_CHANGE, {
                "map_file": self.world_server._map_file,
                "target_x": rx, "target_y": ry,
            })
            session.known_eids.clear()

        # Teleporta no servidor — snap_to_tile cancela tween em andamento e
        # sincroniza pixels/Position (o write manual antigo não resetava
        # is_moving nem os campos de pixel). transfer_player acima já fez
        # o snap se trocou de mapa; chamar de novo aqui é idempotente e
        # cobre o caso comum (já estava no mapa principal).
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
        # Não pode reviver DENTRO de uma partida de Arena — morrer lá vira
        # fantasma de verdade (mesmo fluxo de PvE), mas só sai da morte ao
        # sair da arena de vez (client/arena_handlers.py "Sair da Arena" /
        # ARENA_FORFEIT → MatchProcessorMixin._arena_leave_now já revive
        # nesse momento). Pedido do usuário 20/07/2026.
        if self.world_server._player_match_id.get(player_eid) is not None:
            return
        # Revalida distância no momento do request — não confia na flag cacheada
        if (abs(tm.current_tile_x - gst.corpse_tx) > GHOST_CORPSE_RADIUS_TILES
                or abs(tm.current_tile_y - gst.corpse_ty) > GHOST_CORPSE_RADIUS_TILES):
            return

        self.world_server._revive_player(player_eid, hp_frac=GHOST_CORPSE_REVIVE_HP_FRAC, at_corpse=True)

    async def _handle_char_stats_request(self, session: Session, payload: dict, ts: int) -> None:
        """Jogador abriu o modal de estatísticas — snapshot sob demanda, lido
        direto do componente vivo (CharStatsTracker), sem round-trip de banco
        (mesma razão de não ser um canal contínuo tipo STATS_UPDATE: só muda
        em eventos raros, não vale a pena empurrar a cada tick)."""
        if not session.authenticated:
            return
        player_eid = self.world_server._player_eids.get(session.session_id)
        if player_eid is None:
            return
        from engine.components import CharStatsTracker, QuestLog
        cst = self.world_server.world.get_component(player_eid, CharStatsTracker)
        ql  = self.world_server.world.get_component(player_eid, QuestLog)
        if cst is None:
            return
        await session.send(MsgType.CHAR_STATS_DATA, {
            "pve_damage":       cst.pve_damage,
            "pvp_damage":       cst.pvp_damage,
            "mobs_killed":      cst.mobs_killed,
            "players_killed":   cst.players_killed,
            "duel_wins":        cst.duel_wins,
            "duel_losses":      cst.duel_losses,
            "arena_wins":       dict(cst.arena_wins),
            "arena_losses":     dict(cst.arena_losses),
            "quests_completed": len(ql.completed) if ql else 0,
        })

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
            "is_gm":     session.is_gm,
        })

        near_players = self.world_server.get_players_in_aoi(session.session_id, AOI_RADIUS)
        for p in near_players:
            s2 = self._sessions.get(p.get("session_id", ""))
            if s2:
                _h, _hm = self.world_server.get_player_hp(s2.session_id)
                # level: componente VIVO, não s2.char_data (snapshot do LOGIN
                # do outro player, nunca atualizado — mesma causa raiz do
                # §34.20; aqui é o pior caso: quem loga DEPOIS de alguém já
                # ter subido de nível recebe o level velho no WORLD_STATE
                # inicial, e como o eid já entra em known_eids aqui, o
                # "player ficou visível" nunca re-roda pra corrigir depois —
                # só o tick-broadcast de _sync_player_hp_dirty salvaria, e só
                # no PRÓXIMO level-up que acontecer DEPOIS deste login).
                from engine.components import CharacterStats as _S2CharAOI
                _s2char = self.world_server.world.get_component(s2.entity_id, _S2CharAOI)
                p.update({"name": s2.display_name,
                           "class_id": s2.char_data.get("class_id", "guerreiro"),
                           "hp": _h, "hp_max": _hm,
                           "level": _s2char.level if _s2char else 1, "effects": []})

        _my_map_login = self.world_server.get_player_map(session.session_id)
        # get_mobs_in_aoi já inclui harvestable (Fase M1, revisão 2 —
        # entidade real, mesmo sweep genérico de mob estacionário) — sem
        # isso, um player que loga já dentro do AOI de um item de mapa
        # nunca o veria, pois o sweep de _build_update_for_session só
        # roda dentro de _dispatch_tick_deltas, que exige has_pending.
        near_mobs    = self.world_server.get_mobs_in_aoi(
            tx, ty, AOI_RADIUS, map_file=_my_map_login, viewer_eid=eid,
        )
        all_entities = near_players + near_mobs
        await session.send(MsgType.WORLD_STATE, {
            "tick": self.world_server.tick_count, "tx": tx, "ty": ty,
            "entities": all_entities,
        })
        session.known_eids.add(eid)
        for ent in all_entities:
            session.known_eids.add(ent["eid"])
        # Conteúdo real (itens/coins) do harvestable, personalizado por
        # jogador — mesmo follow-up de LOOT_AVAILABLE que o sweep de tick
        # manda (ver _dispatch_tick_deltas), só que disparado aqui pro caso
        # de login já-dentro-do-AOI.
        for _hent in near_mobs:
            if _hent.get("kind") != "harvestable":
                continue
            _hid = _hent["corpse_id"]
            _hcorpse = self.world_server._corpses.get(_hid)
            if not _hcorpse:
                continue
            _hextra = self.world_server._resolve_conditional_loot_for(_hcorpse, eid)
            await session.send(MsgType.LOOT_AVAILABLE, {
                "corpse_id": _hid,
                "tx":        _hent["tx"], "ty": _hent["ty"],
                "items":     list(_hcorpse.get("items", [])) + _hextra,
                "coins":     _hcorpse.get("coins", 0),
            })

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
        name     = str(payload.get("name", "")).strip()
        class_id = str(payload.get("class_id", "guerreiro"))
        if class_id not in ("guerreiro", "mago", "arqueiro"):
            class_id = "guerreiro"

        from server.auth import create_character as _create_char, _get_conn as _gc
        reason = await _create_char(session.account_id, name, class_id)
        if reason != "ok":
            await session.send(MsgType.CHARACTER_ERROR, {"reason": reason})
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

    async def _handle_suggest_name(self, session: Session,
                                   payload: dict, ts: int = 0) -> None:
        """Sugestão de nome pra caixa de criação de personagem (botão 🎲) —
        precisa ir ao servidor porque só ele sabe quais nomes já existem no
        banco (checagem de unicidade é global, ver server/auth.py::
        _create_character_sync)."""
        if not session.authenticated:
            return
        from server.auth import suggest_character_name as _suggest_name
        name = await _suggest_name()
        await session.send(MsgType.NAME_SUGGESTION, {"name": name})

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
        to_map   = str(payload.get("to_map", "")).replace("\\", "/")
        target_x = int(payload.get("target_x", 0))
        target_y = int(payload.get("target_y", 0))

        # Carrega sob demanda mapas que existem no disco mas nunca foram
        # carregados como bundle standalone (23/07/2026, pedido do usuário
        # — F12→aba Mapa listava TODO maps/*.csv, mas templates só
        # instanciados por partida, ex. arena_poco_negro.csv, nunca tinham
        # bundle próprio fora de uma partida real, então o teleporte de
        # debug pra lá sempre falhava silenciosamente). Validação de
        # caminho (prefixo "maps/", sem "..", extensão .csv) antes de
        # tocar o disco — evita um to_map arbitrário forjado por um
        # cliente malicioso escapando da pasta maps/.
        if to_map not in self.world_server._map_bundles:
            import os as _os_zcr
            if (not to_map.startswith("maps/") or ".." in to_map
                    or not to_map.endswith(".csv") or not _os_zcr.path.exists(to_map)):
                return
            try:
                self.world_server._map_bundles[to_map] = self.world_server._load_map_for(to_map)
            except Exception as _e_zcr:
                log.warning(f"[ZoneChangeReq] falha ao carregar mapa sob demanda {to_map}: {_e_zcr}")
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

    # ── Arena 2x2 (Fase G leva 1, ver server/match_processor.py) ─────────────

    async def _handle_arena_queue_join(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        mode_id = payload.get("mode", "2v2")
        reason  = self.world_server.request_arena_queue_join(eid, mode_id)
        await session.send(MsgType.ARENA_QUEUE_STATE, {
            "in_queue": reason is None,
            **({"mode": mode_id} if reason is None else {"reason": reason}),
        })

    async def _handle_arena_queue_leave(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        self.world_server.request_arena_queue_leave(eid)
        await session.send(MsgType.ARENA_QUEUE_STATE, {"in_queue": False})

    async def _handle_arena_match_accept(self, session: Session, payload: dict, ts: int) -> None:
        """Aceite da janela "Partida encontrada!" (client/arena_handlers.py)
        — entra na arena na hora, sozinho, sem esperar o resto (ver
        MatchProcessorMixin.request_arena_accept). Cliente já fecha o modal
        localmente ao clicar; motivo de recusa (convite expirado/inexistente)
        não precisa de resposta — não há mais nada útil a fazer client-side
        além do que o timeout local já fez."""
        if not session.authenticated:
            return
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        self.world_server.request_arena_accept(eid)

    async def _handle_arena_forfeit(self, session: Session, payload: dict, ts: int) -> None:
        """Comando de chat /forfeit ou /ff (client/arena_handlers.py) —
        desiste da partida atual, sai na hora. O ARENA_MATCH_END (won:False)
        + ZONE_CHANGE de volta saem pelo broadcast loop do próximo tick
        (consume_arena_match_end_events), igual fim de partida normal.
        Cliente já só manda isso sabendo que está numa partida ativa
        (_arena_in_match) — "not_in_match" aqui é só defesa contra cliente
        dessincronizado/modificado, não precisa de resposta."""
        if not session.authenticated:
            return
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        self.world_server.request_arena_forfeit(eid)

    # ── Fila REAL de BG estilo MOBA (04/08/2026, ver server/bg_queue_processor.py) ─

    async def _handle_bg_queue_join(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        reason = self.world_server.request_bg_queue_join(eid)
        await session.send(MsgType.BG_QUEUE_STATE, {
            "in_queue": reason is None,
            **({} if reason is None else {"reason": reason}),
        })

    async def _handle_bg_queue_leave(self, session: Session, payload: dict, ts: int) -> None:
        if not session.authenticated:
            return
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        self.world_server.request_bg_queue_leave(eid)
        await session.send(MsgType.BG_QUEUE_STATE, {"in_queue": False})

    async def _handle_bg_match_accept(self, session: Session, payload: dict, ts: int) -> None:
        """Aceite da janela "Partida encontrada!" — entra na instância na
        hora, sozinho, sem esperar o resto do time (ver
        BgQueueProcessorMixin.request_bg_accept)."""
        if not session.authenticated:
            return
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        self.world_server.request_bg_accept(eid)

    async def _handle_bg_match_leave(self, session: Session, payload: dict, ts: int) -> None:
        """Desiste no meio da luta OU sai da tela de resultado ("Voltar")
        — mesma ação nos 2 casos (ver BgQueueProcessorMixin.
        request_bg_leave). ZONE_CHANGE de volta sai pelo broadcast loop
        do próximo tick (consume_bg_match_leave_events)."""
        if not session.authenticated:
            return
        eid = self.world_server._player_eids.get(session.session_id)
        if eid is None:
            return
        self.world_server.request_bg_leave(eid)

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
        MsgType.SUGGEST_NAME:       _handle_suggest_name,
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
        MsgType.GM_LEVELUP:        _handle_gm_levelup,
        MsgType.GM_ADD_GOLD:       _handle_gm_add_gold,
        MsgType.GM_ADD_ITEM:       _handle_gm_add_item,
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
        MsgType.ARENA_QUEUE_JOIN:    _handle_arena_queue_join,
        MsgType.ARENA_QUEUE_LEAVE:   _handle_arena_queue_leave,
        MsgType.ARENA_MATCH_ACCEPT:  _handle_arena_match_accept,
        MsgType.ARENA_FORFEIT:       _handle_arena_forfeit,
        MsgType.BG_QUEUE_JOIN:       _handle_bg_queue_join,
        MsgType.BG_QUEUE_LEAVE:      _handle_bg_queue_leave,
        MsgType.BG_MATCH_ACCEPT:     _handle_bg_match_accept,
        MsgType.BG_MATCH_LEAVE:      _handle_bg_match_leave,
        MsgType.CHAR_STATS_REQUEST:  _handle_char_stats_request,
    }

    # ── AOI subscription — núcleo do sistema ─────────────────────────────────

    def _on_tick(self, tick_count: int, deltas: dict) -> None:
        if tick_count % (TICK_RATE * 300) == 0 and tick_count > 0:  # autosave a cada 5 min
            asyncio.create_task(self._autosave_all())
        from server import debug_battleground as _dbg_bg_tick
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
                       or bool(self.world_server._pending_sound_events)
                       # Arena (21/07/2026, bug real relatado pelo usuário): nenhum
                       # destes 4 buffers entra em `deltas` nem tinha check próprio
                       # aqui — sem "atividade" normal (ex: ninguém se movendo) no
                       # MESMO tick, o "return" acima descartava a checagem de
                       # pendência inteira e o evento ficava parado no buffer até
                       # ALGUÉM se mexer (o que finalmente fazia `deltas["moved"]`
                       # não-vazio e destravava o dispatch) — sintoma exato: "só
                       # chamou a arena quando movi o personagem", e pior, o
                       # ARENA_MATCH_START do aceite (teleporte já confirmado no
                       # servidor) ficava preso do mesmo jeito, então ninguém via
                       # o ZONE_CHANGE mesmo clicando "Aceitar" a tempo.
                       or bool(self.world_server._arena_match_found_events_this_tick)
                       or bool(self.world_server._arena_match_start_events_this_tick)
                       or bool(self.world_server._arena_match_end_events_this_tick)
                       or bool(self.world_server._arena_match_result_events_this_tick)
                       or bool(self.world_server._arena_gate_open_events_this_tick)
                       # Mesma classe de bug (22/07/2026, achada ao investigar "grupo só
                       # atualiza o HUD quando alguém se move"): estes 4 buffers também são
                       # consumidos aqui embaixo (consume_party_state_events/
                       # consume_duel_end_events/consume_trade_cancellations/
                       # consume_skill_position_corrections) mas nenhum entrava em `deltas`
                       # nem tinha check próprio — sujeitos ao mesmo "preso até atividade
                       # alheia" que já tinha acontecido com a arena.
                       or bool(self.world_server._party_state_events_this_tick)
                       or bool(self.world_server._duel_end_events_this_tick)
                       or bool(self.world_server._trade_cancellations_this_tick)
                       or bool(self.world_server._skill_position_corrections)
                       # Fase M2 (25/07/2026) — mesma classe de bug: harvestable
                       # reabastecido some no buffer até atividade alheia
                       # destravar o dispatch, se não entrar aqui.
                       or bool(self.world_server._pending_harvestable_refill)
                       # Battleground de teste (debug, 01/08/2026) — mesma
                       # classe de bug: sem entrar aqui, o aviso de portão
                       # aberto ficava preso até atividade alheia destravar
                       # o dispatch (ver drain_gate_open_notifications acima).
                       or bool(_dbg_bg_tick._state["pending_gate_open_eids"])
                       # Battleground de teste: Nexus derrubado (placar) e
                       # timeout de saída forçada (02/08/2026) — MESMA
                       # classe de bug, mesmo buffer novo esquecido aqui =
                       # aviso preso até atividade alheia destravar.
                       or bool(_dbg_bg_tick._state["pending_match_result"])
                       or bool(_dbg_bg_tick._state["pending_forced_leave_notify"])
                       # Fila REAL de BG (04/08/2026) — MESMA classe de bug:
                       # os 6 buffers próprios do ciclo de vida da fila
                       # real (server/bg_queue_processor.py) nunca entram
                       # em `deltas`, então precisam estar aqui igual aos
                       # da Arena/debug acima, ou ficam presos até
                       # atividade alheia destravar o dispatch.
                       or bool(self.world_server._bg_match_found_events_this_tick)
                       or bool(self.world_server._bg_match_start_events_this_tick)
                       or bool(self.world_server._bg_gate_open_events_this_tick)
                       or bool(self.world_server._bg_match_leave_events_this_tick)
                       or bool(self.world_server._bg_match_result_events_this_tick))
        if not has_pending:
            return
        asyncio.create_task(self._dispatch_tick_deltas(deltas))

    def _compute_ally_vision_centers(self) -> dict[int, list[tuple[int, int, int]]]:
        """Visão compartilhada de time (SÓ conteúdo instanciado — arena hoje,
        battlefield/dungeon no futuro; NUNCA grupo de mundo aberto).

        Gate: Faction EXPLÍCITA no player (world.get_component direto — NUNCA
        get_entity_faction/resolver, que tem fallback pro default de mundo
        aberto e vazaria visão pra todo mundo). Players comuns não têm
        Faction nenhuma; só ganham ao entrar num contexto de time (mesmo
        padrão que server/match_processor.py já usa pra arena).

        Retorna {player_eid: [(tx, ty, radius), ...]} com a posição+raio de
        cada ALIADO (outro player, torre ou minion com a MESMA Faction +
        MESMA instância/map_file) — nunca a própria posição. Raio por tipo:
        ALLY_VISION_RADIUS_PLAYER/TOWER/MINION (pedido do usuário, torre e
        minion enxergam diferente de um player). Vazio (custo ~zero) fora de
        contexto de time — não toca em _mob_eids se nenhum player tiver
        Faction.
        """
        from engine.components import Faction as _FactionAVC, TileMovement as _TMAvc, Tower as _TowerAVC

        buckets: dict[tuple[str, str], list[tuple[int, int, int, int]]] = {}

        for s in self._sessions.values():
            if not s.authenticated or s.entity_id < 0:
                continue
            fac = self.world_server.world.get_component(s.entity_id, _FactionAVC)
            if fac is None:
                continue
            tm = self.world_server.world.get_component(s.entity_id, _TMAvc)
            if tm is None:
                continue
            map_file = self.world_server.get_player_map(s.session_id)
            buckets.setdefault((map_file, fac.faction_id), []).append(
                (s.entity_id, tm.current_tile_x, tm.current_tile_y, ALLY_VISION_RADIUS_PLAYER))

        if not buckets:
            return {}

        for m_eid in self.world_server._mob_eids:
            fac = self.world_server.world.get_component(m_eid, _FactionAVC)
            if fac is None:
                continue
            map_file = self.world_server.get_entity_map(m_eid)
            key = (map_file, fac.faction_id)
            if key not in buckets:
                continue
            tm = self.world_server.world.get_component(m_eid, _TMAvc)
            if tm is None:
                continue
            # Torre: raio PRÓPRIO por tipo (content/tower_definitions.py::
            # TOWER_TABLE["vision_radius_tiles"], gravado no componente
            # Tower na criação) — não uma constante global única, pedido
            # explícito do usuário (30/07/2026) pra poder ajustar por tipo
            # de torre. ALLY_VISION_RADIUS_TOWER só entra como default de
            # criação (engine/entity_factory.py::create_tower), nunca lido
            # aqui de novo.
            _tower_comp = self.world_server.world.get_component(m_eid, _TowerAVC)
            radius = (_tower_comp.vision_radius_tiles if _tower_comp is not None
                      else ALLY_VISION_RADIUS_MINION)
            buckets[key].append((m_eid, tm.current_tile_x, tm.current_tile_y, radius))

        result: dict[int, list[tuple[int, int, int]]] = {}
        for members in buckets.values():
            if len(members) < 2:
                continue
            for eid_i, _, _, _ in members:
                result[eid_i] = [(tx, ty, radius) for eid_j, tx, ty, radius in members if eid_j != eid_i]
        return result

    async def _flush_stats_updates(self) -> None:
        """Drena e envia TODOS os STATS_UPDATE pendentes (`WorldServer.
        consume_stats_updates`) — chamado tanto pelo dispatch normal por-tick
        (`_dispatch_tick_deltas`) quanto ANTES de um ZONE_CHANGE imediato
        (`_handle_chat`, comando `/testbg`) que não passa pelo dispatch
        batched. Sem o flush antecipado no segundo caso, o cliente processa
        a troca de mapa (`_do_transition`, que dispara autosave no fim) ANTES
        do STATS_UPDATE com o Inventory/Equipment/CharacterStats reais
        restaurados chegar — o autosave prematuro salva a bag/atributos
        ainda "de instância" por cima do save real (bug real, 03/08/2026:
        "perco os itens da bag ao sair da BG" — a bag vazia da instância
        persistia no banco porque `exit_normalized_progression` já tinha
        rodado no servidor, então o guard de `_persist_character` não
        bloqueava mais esse autosave prematuro)."""
        for xp_entry in self.world_server.consume_stats_updates():
            sid = self.world_server.get_session_id_for_player(xp_entry["player_eid"])
            if sid:
                session = self._sessions.get(sid)
                if session and session.authenticated:
                    _payload = dict(xp_entry)
                    _payload["eid"]       = _payload.pop("player_eid")
                    _payload["xp_gained"] = _payload.pop("xp", 0)
                    await session.send(MsgType.STATS_UPDATE, _payload)

    async def _dispatch_tick_deltas(self, deltas: dict) -> None:
        """Distribui deltas para cada cliente respeitando known_eids (AOI subscription)."""
        try:
            self._ally_vision_centers = self._compute_ally_vision_centers()

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
            # Harvestable (Fase M1, revisão 2) entra no MESMO índice de
            # posições que mob estacionário — mesmo sweep genérico de
            # _build_update_for_session descobre os dois, sem código
            # dedicado (_harvestable_eids nunca tem Combatant, por isso é
            # um set PRÓPRIO em vez de misturado em _mob_eids).
            for _me in self.world_server._mob_eids | self.world_server._harvestable_eids:
                _mt = self.world_server.world.get_component(_me, _TM_aoi)
                if _mt:
                    _mob_positions[_me] = (_mt.current_tile_x, _mt.current_tile_y)
                    _mob_hash.insert(_me, _mt.current_tile_x, _mt.current_tile_y)

            # Harvestable com requires_quest setado (Fase M3) — pré-filtrado
            # 1x por tick pra alimentar o sweep de "trava fechou de novo"
            # dentro de _build_update_for_session (ver docstring lá). Restrito
            # só aos gated (não todo _harvestable_eids) pra manter o custo do
            # sweep-por-sessão baixo.
            from engine.components import Harvestable as _Hv_gated
            _gated_harvestable_eids: set = {
                _hv_eid for _hv_eid in self.world_server._harvestable_eids
                if (_hv_comp := self.world_server.world.get_component(_hv_eid, _Hv_gated))
                and _hv_comp.requires_quest
            }

            for session in list(self._sessions.values()):
                if not session.authenticated:
                    continue
                tx, ty = self.world_server.get_tile_pos(session.session_id)
                update = self._build_update_for_session(session, deltas, tx, ty,
                                                        _mob_positions, _mob_hash,
                                                        _gated_harvestable_eids,
                                                        ally_centers=self._ally_vision_centers.get(session.entity_id))
                if update:
                    ok = await session.send(MsgType.AOI_UPDATE, update)
                    if ok:
                        # Harvestable recém-descoberto (Fase M1, 25/07/2026)
                        # — o ENTITY_SPAWN acima só dá posição/nome pra
                        # renderizar; o conteúdo de verdade (itens/coins)
                        # chega aqui, PERSONALIZADO por jogador (mesmo
                        # helper do LOOT_AVAILABLE de corpse de mob — ver
                        # _resolve_conditional_loot_for/§34.51 Fase L1),
                        # pra ele já abrir o modal certo na hora.
                        for _sp in update.get("spawned", []):
                            if _sp.get("kind") != "harvestable":
                                continue
                            _hid = _sp["corpse_id"]   # positivo — id "de verdade", ver sweep acima
                            _hcorpse = self.world_server._corpses.get(_hid)
                            if not _hcorpse:
                                continue
                            _hextra = self.world_server._resolve_conditional_loot_for(
                                _hcorpse, session.entity_id)
                            await session.send(MsgType.LOOT_AVAILABLE, {
                                "corpse_id": _hid,
                                "tx":        _sp["tx"], "ty": _sp["ty"],
                                "items":     list(_hcorpse.get("items", [])) + _hextra,
                                "coins":     _hcorpse.get("coins", 0),
                            })
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
            await self._flush_stats_updates()

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
                # Loot condicional de quest (Fase L1, 25/07/2026) é resolvido
                # POR DESTINATÁRIO aqui — cada um vê SÓ o seu próprio extra
                # (se tiver a quest), nunca o de outro membro do grupo. Já
                # era um loop por destinatário antes disso (bug de grupo
                # nunca existiu neste ponto — só no request_loot(), que
                # devolvia o mesmo item condicional fixo pra qualquer um).
                _loot_recipients = self.world_server.get_party_members(owner_eid) or [owner_eid]
                _loot_corpse_dict = self.world_server._corpses.get(corpse_id, {})
                for _loot_eid in _loot_recipients:
                    _loot_sid = self.world_server.get_session_id_for_player(_loot_eid)
                    _loot_sess = self._sessions.get(_loot_sid) if _loot_sid else None
                    if _loot_sess and _loot_sess.authenticated:
                        _personal_extra = self.world_server._resolve_conditional_loot_for(
                            _loot_corpse_dict, _loot_eid)
                        await _loot_sess.send(MsgType.LOOT_AVAILABLE, {
                            "corpse_id": corpse_id,
                            "tx":        notif_tx,
                            "ty":        notif_ty,
                            "items":     notif["items"] + _personal_extra,
                            "coins":     notif.get("coins", 0),
                        })

            # Harvestable de posição fixa reabastecido (Fase M2, 25/07/2026)
            # — manda LOOT_AVAILABLE personalizado pra quem já conhece a
            # entidade (mesmo princípio do bloco de corpse/loot acima, só
            # que sem ENTITY_SPAWN — a entidade já existe e é permanente,
            # nunca foi despawnada).
            for _refill in self.world_server.consume_harvestable_refills():
                _rf_hid = _refill["hid"]
                _rf_corpse = self.world_server._corpses.get(_rf_hid)
                if not _rf_corpse:
                    continue
                for s in self._sessions_in_aoi(_refill["tx"], _refill["ty"], _refill.get("map")):
                    _rf_extra = self.world_server._resolve_conditional_loot_for(
                        _rf_corpse, s.entity_id)
                    await s.send(MsgType.LOOT_AVAILABLE, {
                        "corpse_id": _rf_hid,
                        "tx":        _refill["tx"], "ty": _refill["ty"],
                        "items":     list(_rf_corpse.get("items", [])) + _rf_extra,
                        "coins":     _rf_corpse.get("coins", 0),
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

            # Arena: fila pareou 2 grupos neste tick (MatchProcessorMixin.
            # _propose_match) — NINGUÉM foi teleportado ainda, só avisa os
            # 4 pra abrir a janela "Partida encontrada!" (aceite manual,
            # ver ARENA_MATCH_ACCEPT/request_arena_accept).
            for _amf in self.world_server.consume_arena_match_found_events():
                _amf_sid  = self.world_server.get_session_id_for_player(_amf["eid"])
                _amf_sess = self._sessions.get(_amf_sid) if _amf_sid else None
                if not (_amf_sess and _amf_sess.authenticated):
                    continue
                await _amf_sess.send(MsgType.ARENA_MATCH_FOUND, {
                    "teammates": _amf["teammates"],
                    "opponents": _amf["opponents"],
                })

            # Arena: player aceitou a partida neste tick
            # (MatchProcessorMixin.request_arena_accept) e já foi
            # teleportado pra instância, sozinho — aqui só avisa o cliente
            # pra carregar o mapa (ZONE_CHANGE, mesmo fluxo de transição de
            # caverna) + manda o contexto da partida (ARENA_MATCH_START) +
            # o tempo de preparo restante (ARENA_COUNTDOWN — contagem é DA
            # PARTIDA, quem entra depois recebe remaining menor/zero).
            for _am_start in self.world_server.consume_arena_match_start_events():
                _am_sid  = self.world_server.get_session_id_for_player(_am_start["eid"])
                _am_sess = self._sessions.get(_am_sid) if _am_sid else None
                if not (_am_sess and _am_sess.authenticated):
                    continue
                await _am_sess.send(MsgType.ZONE_CHANGE, {
                    "map_file": _am_start["map_file"],
                    "target_x": _am_start["target_x"],
                    "target_y": _am_start["target_y"],
                })
                _am_sess.known_eids.clear()
                await _am_sess.send(MsgType.ARENA_MATCH_START, {
                    "map_file":  _am_start["map_file"],
                    "teammates": _am_start["teammates"],
                    "opponents": _am_start["opponents"],
                })
                await _am_sess.send(MsgType.ARENA_COUNTDOWN, {
                    "remaining": _am_start["countdown_remaining"],
                })

            # Arena: portão físico da instância abriu (fim do preparo,
            # MatchProcessorMixin._tick_arena_pending) OU este eid aceitou
            # tarde, já com o portão aberto (request_arena_accept) — avisa
            # o cliente pra trocar a célula do portão localmente (mesmo swap
            # do servidor, ver ARENA_GATE_TILES em shared/constants.py).
            for _ago in self.world_server.consume_arena_gate_open_events():
                _ago_sid  = self.world_server.get_session_id_for_player(_ago["eid"])
                _ago_sess = self._sessions.get(_ago_sid) if _ago_sid else None
                if not (_ago_sess and _ago_sess.authenticated):
                    continue
                await _ago_sess.send(MsgType.ARENA_GATE_OPEN, {})

            # Battleground de teste (debug, 01/08/2026): mesmo aviso de
            # portão aberto acima, mas com `gate_tiles` explícito (tiles
            # DIFERENTES de ARENA_GATE_TILES — ver client/arena_handlers.py::
            # _handle_msg_arena_gate_open) — sem isso o cliente nunca sabia
            # que o portão tinha aberto de verdade (bug real relatado pelo
            # usuário: "fez a contagem mas não abriu o gate").
            from server import debug_battleground as _dbg_bg
            from server.bg_queue_processor import BG_QUEUE_GATE_TILES
            if _dbg_bg._state["pending_gate_open_eids"]:
                _dbg_gate_tiles = [list(t) for tiles in _dbg_bg.DEBUG_BG_GATE_TILES.values()
                                   for t in tiles]
                for _dbg_eid in _dbg_bg.drain_gate_open_notifications():
                    _dbg_sid  = self.world_server.get_session_id_for_player(_dbg_eid)
                    _dbg_sess = self._sessions.get(_dbg_sid) if _dbg_sid else None
                    if not (_dbg_sess and _dbg_sess.authenticated):
                        continue
                    await _dbg_sess.send(MsgType.ARENA_GATE_OPEN, {"gate_tiles": _dbg_gate_tiles})

            # Arena: player saiu da partida neste tick (/forfeit, botão
            # "Sair da Arena", desconexão, ou timeout automático da tela
            # de resultado — sempre via MatchProcessorMixin._arena_leave_
            # now) — ele já foi teleportado de volta; avisa o cliente pra
            # trocar de mapa/posição + o resultado (won).
            for _am_end in self.world_server.consume_arena_match_end_events():
                _ame_sid  = self.world_server.get_session_id_for_player(_am_end["eid"])
                _ame_sess = self._sessions.get(_ame_sid) if _ame_sid else None
                if not (_ame_sess and _ame_sess.authenticated):
                    continue
                await _ame_sess.send(MsgType.ZONE_CHANGE, {
                    "map_file": _am_end["map_file"],
                    "target_x": _am_end["target_x"],
                    "target_y": _am_end["target_y"],
                })
                _ame_sess.known_eids.clear()
                await _ame_sess.send(MsgType.ARENA_MATCH_END, {"won": _am_end["won"]})

            # Arena: partida DECIDIDA neste tick (time inteiro eliminado
            # ou esvaziado) — NÃO teleporta ninguém ainda (isso só
            # acontece quando cada um sai, ver bloco acima); só manda o
            # placar pra montar o modal de fim de partida (estilo WoW).
            for _am_res in self.world_server.consume_arena_match_result_events():
                _amr_sid  = self.world_server.get_session_id_for_player(_am_res["eid"])
                _amr_sess = self._sessions.get(_amr_sid) if _amr_sid else None
                if not (_amr_sess and _amr_sess.authenticated):
                    continue
                await _amr_sess.send(MsgType.ARENA_MATCH_RESULT, {"results": _am_res["results"]})

            # Battleground de teste (debug, 02/08/2026): Nexus derrubado —
            # placar final dos dois times (não teleporta ninguém ainda,
            # mesma forma de ARENA_MATCH_RESULT acima).
            for _bg_eid, _bg_payload in _dbg_bg.drain_match_result_notifications():
                _bgr_sid  = self.world_server.get_session_id_for_player(_bg_eid)
                _bgr_sess = self._sessions.get(_bgr_sid) if _bgr_sid else None
                if not (_bgr_sess and _bgr_sess.authenticated):
                    continue
                await _bgr_sess.send(MsgType.BG_MATCH_RESULT, _bg_payload)

            # Battleground de teste: timeout de 15s da tela de resultado
            # forçou _leave() de quem não clicou "Voltar" — já foi
            # teleportado de volta (transfer_player, dentro de _leave);
            # só falta avisar via ZONE_CHANGE, mesma forma do bloco
            # ARENA_MATCH_END acima (saída manual via botão/"/testbg leave"
            # já manda ZONE_CHANGE pelo caminho normal de chat, sem
            # precisar deste bloco).
            for _bgf_eid, _bgf_zone in _dbg_bg.drain_forced_leave_notify():
                _bgf_sid  = self.world_server.get_session_id_for_player(_bgf_eid)
                _bgf_sess = self._sessions.get(_bgf_sid) if _bgf_sid else None
                if not (_bgf_sess and _bgf_sess.authenticated):
                    continue
                await _bgf_sess.send(MsgType.ZONE_CHANGE, _bgf_zone)
                _bgf_sess.known_eids.clear()

            # Fila REAL de BG (04/08/2026, server/bg_queue_processor.py) —
            # mesmo padrão 1-pra-1 do bloco Arena acima, só que pros 6
            # eventos próprios do ciclo de vida da fila real (não mais o
            # estado único compartilhado de debug_battleground.py).
            for _bqf in self.world_server.consume_bg_match_found_events():
                _bqf_sid  = self.world_server.get_session_id_for_player(_bqf["eid"])
                _bqf_sess = self._sessions.get(_bqf_sid) if _bqf_sid else None
                if not (_bqf_sess and _bqf_sess.authenticated):
                    continue
                await _bqf_sess.send(MsgType.BG_MATCH_FOUND, {
                    "team_size": _bqf["team_size"],
                    "teammates": _bqf["teammates"],
                    "opponents": _bqf["opponents"],
                })

            for _bqs in self.world_server.consume_bg_match_start_events():
                _bqs_sid  = self.world_server.get_session_id_for_player(_bqs["eid"])
                _bqs_sess = self._sessions.get(_bqs_sid) if _bqs_sid else None
                if not (_bqs_sess and _bqs_sess.authenticated):
                    continue
                await _bqs_sess.send(MsgType.ZONE_CHANGE, {
                    "map_file": _bqs["map_file"],
                    "target_x": _bqs["target_x"],
                    "target_y": _bqs["target_y"],
                })
                _bqs_sess.known_eids.clear()
                await _bqs_sess.send(MsgType.BG_MATCH_START, {
                    "map_file":  _bqs["map_file"],
                    "team_size": _bqs["team_size"],
                    "teammates": _bqs["teammates"],
                    "opponents": _bqs["opponents"],
                })
                await _bqs_sess.send(MsgType.ARENA_COUNTDOWN, {
                    "remaining":  _bqs["countdown_remaining"],
                    "my_faction": _bqs["my_faction"],
                })

            if self.world_server._bg_gate_open_events_this_tick:
                _bq_gate_tiles = [list(t) for tiles in BG_QUEUE_GATE_TILES.values()
                                  for t in tiles]
                for _bqg in self.world_server.consume_bg_gate_open_events():
                    _bqg_sid  = self.world_server.get_session_id_for_player(_bqg["eid"])
                    _bqg_sess = self._sessions.get(_bqg_sid) if _bqg_sid else None
                    if not (_bqg_sess and _bqg_sess.authenticated):
                        continue
                    await _bqg_sess.send(MsgType.ARENA_GATE_OPEN, {"gate_tiles": _bq_gate_tiles})

            for _bql in self.world_server.consume_bg_match_leave_events():
                _bql_sid  = self.world_server.get_session_id_for_player(_bql["eid"])
                _bql_sess = self._sessions.get(_bql_sid) if _bql_sid else None
                if not (_bql_sess and _bql_sess.authenticated):
                    continue
                await _bql_sess.send(MsgType.ZONE_CHANGE, {
                    "map_file": _bql["map_file"],
                    "target_x": _bql["target_x"],
                    "target_y": _bql["target_y"],
                })
                _bql_sess.known_eids.clear()

            for _bqr_eid, _bqr_payload in self.world_server.consume_bg_match_result_events():
                _bqr_sid  = self.world_server.get_session_id_for_player(_bqr_eid)
                _bqr_sess = self._sessions.get(_bqr_sid) if _bqr_sid else None
                if not (_bqr_sess and _bqr_sess.authenticated):
                    continue
                await _bqr_sess.send(MsgType.BG_MATCH_RESULT, _bqr_payload)

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
                                   mob_hash: "_SpatialHash | None" = None,
                                   gated_harvestable_eids: "set | None" = None,
                                   ally_centers: "list[tuple[int,int,int]] | None" = None) -> dict:
        """
        Constrói AOI_UPDATE para uma sessão específica, com subscription tracking:
        - Entidade entra no AOI → ENTITY_SPAWN + adiciona a known_eids
        - Entidade sai do AOI  → ENTITY_DESPAWN + remove de known_eids
        - Entidade em AOI conhecida → ENTITY_MOVE

        ally_centers: visão compartilhada de time (SessionManager.
        _compute_ally_vision_centers, instanciado — arena/battlefield/
        dungeon). Cada tupla (tx, ty, raio) é um centro EXTRA de AOI, além
        da posição própria (cx, cy) — a entidade entra se estiver dentro de
        QUALQUER um dos centros. Recalculado do zero todo tick a partir do
        estado atual, então nunca fica "preso" a um aliado que já saiu.
        """
        r      = AOI_RADIUS
        r_exit = AOI_RADIUS + AOI_EXIT_BUFFER
        result: dict = {}

        # Mapa atual do player que recebe este update.
        _my_map = self.world_server.get_player_map(session.session_id)

        from engine.components import MapLocation as _ML_aoi

        _centers      = [(cx, cy, r)] + list(ally_centers or ())
        _centers_exit = [(cx, cy, r_exit)] + [(ax, ay, ar + AOI_EXIT_BUFFER)
                                               for ax, ay, ar in (ally_centers or ())]

        def in_aoi(tx: int, ty: int, eid: int = -1) -> bool:
            # Métrica canônica: Chebyshev (utils.in_aoi) — mesma dos broadcasts
            # diretos (_sessions_in_aoi) e do spawn inicial (WORLD_STATE).
            # Antes era círculo Euclidiano só aqui: entidades nos "cantos" do
            # quadrado entravam por um critério e não pelo outro.
            # "Qualquer centro" (posição própria OU visão de aliado) cobre —
            # ver docstring de ally_centers acima.
            if not any(_in_aoi(ccx, ccy, tx, ty, crad) for ccx, ccy, crad in _centers):
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
            if not any(_in_aoi(ccx, ccy, tx, ty, crad) for ccx, ccy, crad in _centers_exit):
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
            # Projéteis de mob: entrega ao dono do alvo (sempre, sem AOI
            # check — garante que quem está sendo atirado veja o projétil
            # mesmo raspando a borda do raio) E a qualquer sessão com o
            # projétil dentro do AOI + mesmo mapa (21/07/2026 — antes SÓ o
            # alvo recebia; um NPC de serviço atirando num MOB nunca tinha
            # sessão-alvo, então a flecha era invisível pra todo mundo;
            # espectador também nunca via flecha mirando OUTRO player).
            # in_aoi sem eid: projétil não tem MapLocation — o mapa é
            # validado pelo mapa do ATACANTE. São transientes — não entram
            # em known_eids (sem despawn assimétrico).
            if sp.get("kind") == "mob_projectile":
                _proj_same_map = (self.world_server.get_entity_map(
                    sp.get("attacker_seid", -1)) == _my_map)
                if (sp.get("target_seid") == session.entity_id
                        or (_proj_same_map and in_aoi(sp["tx"], sp["ty"]))):
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
            # União dos candidatos de CADA centro (posição própria + visão de
            # aliados) — um mob perto de um teammate mas longe de cx,cy não
            # pode ficar de fora só porque a consulta usou o raio errado.
            # max(r, ...) garante que a busca no hash nunca é mais estreita
            # que o maior raio possível entre os centros; o filtro exato
            # (centro certo + raio certo) continua sendo feito por in_aoi().
            _max_r = max((crad for _, _, crad in _centers), default=r)
            _candidates: set = set()
            for ccx, ccy, _ in _centers:
                _candidates |= mob_hash.nearby(ccx, ccy, _max_r)
        else:
            _candidates = mob_positions.keys() if mob_positions else ()
        for mob_eid in _candidates:
            if mob_eid in session.known_eids:
                continue
            pos = mob_positions.get(mob_eid) if mob_positions else None
            if pos and in_aoi(pos[0], pos[1], mob_eid):
                # Fase M3 (25/07/2026): harvestable com requires_quest
                # fica de fora — nem entra em "spawned" nem em
                # known_eids, então o PRÓPRIO sweep descobre sozinho no
                # tick seguinte se o player aceitar a quest depois (sem
                # precisar de código extra pra "revelar").
                if not self.world_server._harvestable_visible_to(mob_eid, session.entity_id):
                    continue
                spawn_data = self.world_server.get_entity_spawn_data(mob_eid)
                if spawn_data:
                    result.setdefault("spawned", []).append(spawn_data)
                    session.known_eids.add(mob_eid)

        # Sweep: harvestable com trava de quest JÁ conhecido, mas a trava
        # FECHOU de novo (quest completada/entregue, saiu de QuestLog.active)
        # — bug real relatado pelo usuário 25/07/2026: completou a quest, o
        # harvestable devia sumir de novo e não sumia. O sweep ACIMA só
        # cobre "revelar" (entidade ainda fora de known_eids); esconder de
        # novo depois de já ter sido descoberta nunca tinha um passo
        # dedicado, porque esse sweep pula tudo que já está em known_eids
        # (linha ~2602). gated_harvestable_eids (pré-filtrado 1x por tick em
        # _dispatch_tick_deltas — só harvestable com requires_quest setado,
        # não TODO harvestable) mantém o custo restrito a quem realmente
        # pode precisar reavaliar.
        if gated_harvestable_eids:
            for _gh_eid in (session.known_eids & gated_harvestable_eids):
                if not self.world_server._harvestable_visible_to(_gh_eid, session.entity_id):
                    session.known_eids.discard(_gh_eid)
                    result.setdefault("despawned", []).append(_gh_eid)

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
        from engine.components import CharacterStats as _CharSweep, GhostState as _GSTsweep
        for _cpeid, _cpdata in list(getattr(self.world_server, "_player_corpses", {}).items()):
            _seid = _PCEB + _cpeid
            if _seid in session.known_eids:
                continue
            # Só entrega o marcador sintético de corpo DEPOIS que o espírito
            # foi liberado (GhostState.is_ghost=True) — antes disso, a
            # entidade REAL do player já está tingida de cadáver no mesmo
            # tile (_handle_player_death), então entregar o marcador aqui
            # criava um SEGUNDO corpo sobreposto, que só "sumia" (um dos
            # dois) quando a entidade real era despawnada ao liberar o
            # espírito (bug real relatado pelo usuário 22/07/2026: "corpo
            # duplicado ao morrer, um dos corpos some ao liberar o
            # espírito"). `_player_corpses[eid]` é gravado no INSTANTE da
            # morte (respawn_system.py::_handle_player_death), bem antes
            # do espírito ser liberado — daí a janela de duplicata.
            _cgst = self.world_server.world.get_component(_cpeid, _GSTsweep)
            if not (_cgst and _cgst.is_ghost):
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

        # Visão compartilhada de time (30/07/2026): cliente revela a névoa
        # (minimap + LOS) ao redor de cada aliado, exatamente como a própria
        # posição — servidor decide QUEM/RAIO, cliente só desenha (mesmo
        # princípio de "client nunca calcula gameplay"). Só inclui quando
        # não-vazio pra não gerar AOI_UPDATE todo tick fora de contexto de
        # time (custo ~zero no caso comum, mundo aberto).
        if ally_centers:
            result["ally_vision_centers"] = [[ax, ay, ar] for ax, ay, ar in ally_centers]

        return result

    # ── Player deaths — enviados diretamente, não via AOI_UPDATE ─────────────

    async def _autosave_all(self) -> None:
        """Autosave periódico (a cada 5 min, ver `_on_tick`) — via
        `_persist_character`, que pula sozinho qualquer sessão dentro de
        uma instância de progressão normalizada (ver docstring lá).
        ANTES desta correção (02/08/2026) este era um dos 4 pontos de
        save que nunca verificava — o incidente real que motivou o fix."""
        saved = 0
        for session in list(self._sessions.values()):
            if await self._persist_character(session, context="autosave"):
                saved += 1
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
        - Visão compartilhada de time (instanciado): uma sessão também
          recebe o evento se ele estiver dentro do raio de um ALIADO dela
          (self._ally_vision_centers, recomputado 1x/tick) — cobre chat de
          proximidade, sons e skills de teammates fora do próprio AOI, sem
          exceção (pedido do usuário: chat também viaja pela visão do time).
        """
        result = []
        for s in list(self._sessions.values()):
            if not s.authenticated or s.session_id == exclude_sid:
                continue
            if (map_file is not None
                    and self.world_server.get_player_map(s.session_id) != map_file):
                continue
            sx, sy = self.world_server.get_tile_pos(s.session_id)
            _centers = [(sx, sy, AOI_RADIUS)] + self._ally_vision_centers.get(s.entity_id, [])
            if not any(_in_aoi(tx, ty, ccx, ccy, crad) for ccx, ccy, crad in _centers):
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
