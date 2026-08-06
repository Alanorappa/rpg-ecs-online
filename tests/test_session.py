"""
tests/test_session.py
Testes do SessionManager e AOI subscription.

Usa FakeSession (sem WebSocket real) para capturar mensagens enviadas
e verificar que o servidor envia o que deve, para quem deve.

Uso:
    py -3.10 tests/test_session.py
"""
import os, sys, asyncio, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob, set_entity_tile
from shared.messages import MsgType


# ─────────────────────────────────────────────────────────────────────────────
# FakeSession: captura mensagens sem WebSocket
# ─────────────────────────────────────────────────────────────────────────────

class FakeWS:
    """Simula um WebSocket. Captura mensagens em self.sent."""
    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    async def send(self, data: str):
        self.sent.append(data)

    async def close(self):
        self.closed = True

    def __aiter__(self): return self
    async def __anext__(self): raise StopAsyncIteration


def make_session_manager(ws=None):
    """Cria WorldServer + SessionManager prontos para uso."""
    from server.session import SessionManager
    ws = ws or make_world_server()
    mgr = SessionManager(ws)
    return ws, mgr


def _valid_char_name(username: str) -> str:
    """Deriva um nome de personagem válido a partir do username de teste.
    Usernames de teste têm underscore/número (ex: "user_ap_test") — desde
    a validação de formato em server/auth.py::_create_character_sync
    (feedback do usuário, 20/07/2026: nome só letras, 3-16 chars), esses
    usernames crus não servem mais de NOME de personagem. Login continua
    usando o username cru (conta); só o campo `name` do personagem
    precisa passar em is_valid_name."""
    from shared.character_names import NAME_MAX_LEN
    letters = "".join(c for c in username if c.isalpha())
    if len(letters) < 3:
        letters = (letters + "Testchar")
    return letters[:NAME_MAX_LEN]


async def fake_login(mgr, session_id: str, username: str,
                     tile_x: int = 115, tile_y: int = 389,
                     class_id: str = "guerreiro") -> tuple:
    """
    Simula o fluxo completo de login de um jogador (fluxo atual:
    LOGIN → AUTH_OK{characters} → SELECT_CHARACTER → LOGIN_OK/WORLD_STATE).
    Retorna (session, fake_ws).
    """
    from server.auth import (_register_account_sync, _get_account_id_sync,
                             _create_character_sync, _hash)
    from shared.messages import encode, MsgType
    from shared.constants import PROTOCOL_VERSION

    # Garante que conta + personagem existem no banco
    pw_hash = _hash("test123")
    _register_account_sync(username, pw_hash)   # no-op se já existe
    acc_id = _get_account_id_sync(username)
    _create_character_sync(acc_id, _valid_char_name(username), class_id, tile_x, tile_y)

    fake_ws = FakeWS()
    session = await mgr.on_connect(fake_ws, session_id)

    # LOGIN → AUTH_OK com a lista de personagens
    await mgr.on_message(session, encode(MsgType.LOGIN, {
        "username": username, "password": pw_hash, "version": PROTOCOL_VERSION
    }))
    auth_msgs = get_msgs_of_type(fake_ws, MsgType.AUTH_OK)
    chars = auth_msgs[0].get("characters", []) if auth_msgs else []
    if chars:
        # Seleciona o primeiro personagem → spawn + LOGIN_OK + WORLD_STATE
        await mgr.on_message(session, encode(MsgType.SELECT_CHARACTER, {
            "char_id": chars[0]["id"]
        }))

    # Expira a imunidade/invisibilidade pós-login (3s) — sem isso, broadcasts
    # pra/sobre este player são filtrados por _can_see e dano vira
    # blocked_immune nos primeiros 90 ticks. Ver tests/helpers.py::
    # clear_login_immunity (mesma razão, mesma causa raiz dos 7F da suíte).
    if session.entity_id != -1:
        from tests.helpers import clear_login_immunity
        clear_login_immunity(mgr.world_server, session.entity_id)

    return session, fake_ws


def get_messages(fake_ws: FakeWS) -> list[tuple]:
    """Desserializa todas as mensagens capturadas pela FakeWS."""
    from shared.messages import decode
    result = []
    for raw in fake_ws.sent:
        try:
            result.append(decode(raw))
        except Exception:
            pass
    return result   # lista de (MsgType, payload, seq, ts)


def get_msgs_of_type(fake_ws: FakeWS, msg_type: MsgType) -> list[dict]:
    return [payload for mt, payload, _, _ in get_messages(fake_ws) if mt == msg_type]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Login e sessão
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildSaveMergeFog(unittest.TestCase):
    """_build_save_merge — union de fog (servidor + cliente) com o novo
    codec bitmap+zlib (shared/fog_codec.py, bug real 19/07/2026). Cobre a
    migração transparente: DB pode ter o formato ANTIGO (lista de
    coordenadas) de personagens salvos antes do fix."""

    def test_fog_do_cliente_sozinho_vira_formato_novo(self):
        from server.session import SessionManager
        from shared.fog_codec import encode_fog, decode_fog
        cli_fog = encode_fog({"maps/map_1.csv": {(1, 1), (2, 2)}})
        merged = SessionManager._build_save_merge(
            {"fog_json": "{}"}, {"fog": cli_fog})
        self.assertEqual(decode_fog(merged["fog"])["maps/map_1.csv"], {(1, 1), (2, 2)})

    def test_fog_antigo_no_banco_faz_union_com_fog_novo_do_cliente(self):
        """Personagem salvo ANTES do fix (fog_json em formato de lista) —
        próximo save precisa unir com o que o cliente manda (já no formato
        novo) sem perder nada, e persistir tudo no formato novo."""
        import json
        from server.session import SessionManager
        from shared.fog_codec import encode_fog, decode_fog
        srv_data = {"fog_json": json.dumps({"maps/map_1.csv": [[1, 1], [2, 2]]})}
        cli_fog  = encode_fog({"maps/map_1.csv": {(2, 2), (3, 3)}})
        merged = SessionManager._build_save_merge(srv_data, {"fog": cli_fog})
        self.assertEqual(decode_fog(merged["fog"])["maps/map_1.csv"],
                         {(1, 1), (2, 2), (3, 3)})

    def test_sem_payload_do_cliente_mantem_fog_do_servidor(self):
        import json
        from server.session import SessionManager
        from shared.fog_codec import decode_fog
        srv_data = {"fog_json": json.dumps({"maps/map_1.csv": [[9, 9]]})}
        merged = SessionManager._build_save_merge(srv_data, {})
        self.assertEqual(decode_fog(merged["fog"])["maps/map_1.csv"], {(9, 9)})


class TestLogin(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    async def test_login_sends_login_ok(self):
        """Login válido deve retornar LOGIN_OK."""
        _, fw = await fake_login(self.mgr, "s1", "user_login_ok")
        msgs = get_msgs_of_type(fw, MsgType.LOGIN_OK)
        self.assertEqual(len(msgs), 1, "LOGIN_OK não enviado")

    async def test_login_ok_contains_eid(self):
        """LOGIN_OK deve conter entity_id do player."""
        _, fw = await fake_login(self.mgr, "s1", "user_eid")
        msg = get_msgs_of_type(fw, MsgType.LOGIN_OK)[0]
        self.assertIn("eid", msg)
        self.assertGreater(msg["eid"], -1)

    async def test_login_sends_world_state(self):
        """Após login, cliente recebe WORLD_STATE com snapshot inicial."""
        _, fw = await fake_login(self.mgr, "s1", "user_ws")
        msgs = get_msgs_of_type(fw, MsgType.WORLD_STATE)
        self.assertEqual(len(msgs), 1, "WORLD_STATE não enviado após login")

    async def test_login_ok_contains_hp_and_hp_max(self):
        """LOGIN_OK deve conter hp e hp_max para o cliente sincronizar."""
        _, fw = await fake_login(self.mgr, "s1", "user_hpsync")
        msg = get_msgs_of_type(fw, MsgType.LOGIN_OK)[0]
        self.assertIn("hp",     msg, "LOGIN_OK sem campo 'hp'")
        self.assertIn("hp_max", msg, "LOGIN_OK sem campo 'hp_max'")
        self.assertGreater(msg["hp_max"], 0, "hp_max deve ser > 0")
        self.assertEqual(msg["hp"], msg["hp_max"], "hp inicial deve ser = hp_max (servidor inicia full)")

    async def test_client_ap_ignored_by_server(self):
        """Servidor NÃO deve adotar AP/max_hp forjados no LOGIN — stats são
        derivados server-side (Equipment/TalentTree/level). O teste antigo
        assertava o comportamento inverso, que era a vulnerabilidade
        PLAYER_STAT_SYNC (removida — ver PROBLEMAS_ARQUITETURA.md)."""
        from server.auth import (_register_account_sync, _get_account_id_sync,
                                 _create_character_sync, _hash)
        from shared.messages import encode
        from shared.constants import PROTOCOL_VERSION
        ph = _hash("test123")
        _register_account_sync("user_ap_test", ph)
        acc_id = _get_account_id_sync("user_ap_test")
        _create_character_sync(acc_id, _valid_char_name("user_ap_test"), "guerreiro", 115, 389)

        fake_ws = FakeWS()
        session = await self.mgr.on_connect(fake_ws, "s_ap")
        # Tenta forjar AP/max_hp absurdos no payload de LOGIN
        await self.mgr.on_message(session, encode(MsgType.LOGIN, {
            "username": "user_ap_test", "password": ph,
            "version": PROTOCOL_VERSION, "ap": 99999.0, "max_hp": 999999,
        }))
        auth_msgs = get_msgs_of_type(fake_ws, MsgType.AUTH_OK)
        chars = auth_msgs[0].get("characters", []) if auth_msgs else []
        self.assertTrue(chars, "AUTH_OK sem personagens")
        await self.mgr.on_message(session, encode(MsgType.SELECT_CHARACTER, {
            "char_id": chars[0]["id"]
        }))

        from engine.components import CombatStats
        eid = self.mgr.world_server._player_eids.get("s_ap")
        self.assertIsNotNone(eid)
        cs = self.mgr.world_server.world.get_component(eid, CombatStats)
        self.assertLess(cs.attack_power, 99999.0,
                        "Servidor adotou AP forjado do cliente (vulnerabilidade)")
        self.assertLess(cs.max_hp, 999999,
                        "Servidor adotou max_hp forjado do cliente (vulnerabilidade)")

    async def test_auth_ok_nao_manda_blobs_json_pesados(self):
        """Bug real relatado pelo usuário 19/07/2026: conexão caía logo após
        autenticar com "sent 1009 (message too big)". Causa raiz:
        AUTH_OK mandava a linha INTEIRA de `characters` (SELECT *) — inclui
        fog_json (grid de fog-of-war, cresce sem limite por tile explorado)
        e outros blobs JSON grandes, que não fazem falta nenhuma na tela de
        seleção de personagem (só usa id/name/class_id/level — ver
        ui/char_creation_screen.py; _handle_select_character busca o
        personagem escolhido de novo, por inteiro, via get_character()).
        Somados nos 3 personagens de uma conta bem testada, passava do
        limite de 1 MB do frame WebSocket. Fix: authenticate() só seleciona
        as 4 colunas leves do banco."""
        from server.auth import (_register_account_sync, _get_account_id_sync,
                                 _create_character_sync, _hash, _get_conn)
        from shared.messages import encode
        from shared.constants import PROTOCOL_VERSION
        ph = _hash("test123")
        _register_account_sync("user_fog_grande", ph)
        acc_id = _get_account_id_sync("user_fog_grande")
        _create_character_sync(acc_id, _valid_char_name("user_fog_grande"), "guerreiro", 115, 389)

        # Simula fog_json inchado (como um personagem MUITO explorado teria).
        _fog_grande = "x" * 600_000
        with _get_conn() as conn:
            conn.execute("UPDATE characters SET fog_json=? WHERE account_id=?",
                        (_fog_grande, acc_id))

        fake_ws = FakeWS()
        session = await self.mgr.on_connect(fake_ws, "s_fog")
        await self.mgr.on_message(session, encode(MsgType.LOGIN, {
            "username": "user_fog_grande", "password": ph, "version": PROTOCOL_VERSION,
        }))
        auth_msgs = get_msgs_of_type(fake_ws, MsgType.AUTH_OK)
        self.assertEqual(len(auth_msgs), 1, "AUTH_OK não enviado")
        chars = auth_msgs[0].get("characters", [])
        self.assertTrue(chars, "AUTH_OK sem personagens")
        for c in chars:
            self.assertEqual(set(c.keys()), {"id", "name", "class_id", "level"},
                            "AUTH_OK.characters vazando campos além do necessário pra seleção")
        import json as _json_test
        raw = _json_test.dumps(auth_msgs[0]).encode()
        self.assertLess(len(raw), 100_000,
                        "AUTH_OK muito grande mesmo com fog_json inchado no banco")

    async def test_world_state_contains_entities_list(self):
        """WORLD_STATE deve ter campo 'entities'."""
        _, fw = await fake_login(self.mgr, "s1", "user_ent")
        msg = get_msgs_of_type(fw, MsgType.WORLD_STATE)[0]
        self.assertIn("entities", msg)

    async def test_invalid_credentials_returns_error(self):
        """Credenciais inválidas devem retornar LOGIN_ERROR."""
        from shared.messages import encode
        fake_ws = FakeWS()
        session = await self.mgr.on_connect(fake_ws, "s_bad")
        bad_login = encode(MsgType.LOGIN, {
            "username": "nonexistent_xyz", "password": "wronghash", "version": 1
        })
        await self.mgr.on_message(session, bad_login)
        errors = get_msgs_of_type(fake_ws, MsgType.LOGIN_ERROR)
        self.assertEqual(len(errors), 1, "LOGIN_ERROR não enviado para credencial inválida")

    async def test_double_login_same_user_returns_error(self):
        """Mesmo usuário não pode logar duas vezes simultaneamente."""
        await fake_login(self.mgr, "s1", "user_double")
        _, fw2 = await fake_login(self.mgr, "s2", "user_double")
        errors = get_msgs_of_type(fw2, MsgType.LOGIN_ERROR)
        self.assertEqual(len(errors), 1, "LOGIN_ERROR não enviado para login duplicado")


# ─────────────────────────────────────────────────────────────────────────────
# 2. AOI subscription — known_eids
# ─────────────────────────────────────────────────────────────────────────────

class TestAOISubscription(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        # Precisa de player para SpawnZoneSystem ativar zonas (usa posição do player)
        await fake_login(self.mgr, "seed", "seed_user", 130, 374)
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_ticks(self.ws_server, 50))
        await self.mgr.on_disconnect("seed")

    async def test_known_eids_populated_after_login(self):
        """Após login, session.known_eids deve conter mobs no AOI."""
        session, _ = await fake_login(self.mgr, "s1", "user_known", 130, 374)
        # Alguns mobs devem estar no AOI de (130,374)
        mob_eids_in_known = session.known_eids & self.ws_server._mob_eids
        self.assertGreater(len(mob_eids_in_known), 0,
                           "Nenhum mob em known_eids após login próximo à zona")

    async def test_world_state_includes_mobs_in_aoi(self):
        """WORLD_STATE deve incluir mobs que já existem no AOI."""
        _, fw = await fake_login(self.mgr, "s1", "user_mobs_ws", 130, 374)
        ws_msg = get_msgs_of_type(fw, MsgType.WORLD_STATE)[0]
        mob_entities = [e for e in ws_msg["entities"] if e.get("kind") == "enemy"]
        self.assertGreater(len(mob_entities), 0,
                           "WORLD_STATE sem mobs próximos ao player (130,374)")

    async def test_distant_player_does_not_see_mobs(self):
        """Player distante dos mobs não deve receber mobs no WORLD_STATE."""
        _, fw = await fake_login(self.mgr, "s1", "user_far", 10, 10)
        ws_msg = get_msgs_of_type(fw, MsgType.WORLD_STATE)[0]
        mob_entities = [e for e in ws_msg["entities"] if e.get("kind") == "enemy"]
        self.assertEqual(len(mob_entities), 0,
                         "Player em (10,10) não deveria ver mobs da zona (130,374)")

    async def test_second_player_sees_first_player(self):
        """Quando player B loga perto de A, A deve aparecer no WORLD_STATE de B."""
        session_a, _ = await fake_login(self.mgr, "s1", "user_a", 115, 389)
        _, fw_b = await fake_login(self.mgr, "s2", "user_b", 117, 389)
        ws_b = get_msgs_of_type(fw_b, MsgType.WORLD_STATE)[0]
        player_eids = [e["eid"] for e in ws_b["entities"] if e.get("kind") == "player"]
        self.assertIn(session_a.entity_id, player_eids,
                      "Player A não apareceu no WORLD_STATE de B")

    async def test_world_state_mostra_level_atual_nao_o_de_login(self):
        """Bug real relatado pelo usuário 17/07/2026: mago que loga DEPOIS
        do guerreiro já ter subido de nível recebia o level de LOGIN do
        guerreiro no WORLD_STATE inicial (Session.char_data["level"],
        nunca atualizado durante a sessão) — e como o eid já entra em
        known_eids ali, o "player ficou visível" nunca re-roda depois pra
        corrigir (só o próximo level-up, via broadcast de tick, salvaria)."""
        session_a, _ = await fake_login(self.mgr, "s1", "user_lvl_a", 115, 389)
        from engine.components import CharacterStats
        char_a = self.ws_server.world.get_component(session_a.entity_id, CharacterStats)
        char_a.level = 10   # progressão real DEPOIS do login de A (char_data não sabe)

        _, fw_b = await fake_login(self.mgr, "s2", "user_lvl_b", 117, 389)
        ws_b = get_msgs_of_type(fw_b, MsgType.WORLD_STATE)[0]
        player_a_entity = next(e for e in ws_b["entities"]
                              if e.get("kind") == "player" and e["eid"] == session_a.entity_id)
        self.assertEqual(player_a_entity["level"], 10,
                         "WORLD_STATE deveria mostrar o level ATUAL de A, não o de login")

    async def test_second_player_in_known_eids_of_first(self):
        """Quando B loga perto de A, o eid de B deve ir para known_eids de A."""
        session_a, _ = await fake_login(self.mgr, "s1", "user_ka", 115, 389)
        session_b, _ = await fake_login(self.mgr, "s2", "user_kb", 117, 389)
        self.assertIn(session_b.entity_id, session_a.known_eids,
                      "Eid de B não está em known_eids de A")

    async def test_first_player_receives_entity_spawn_when_b_logs_in(self):
        """Quando B loga perto de A, A deve receber ENTITY_SPAWN de B.

        Desde a imunidade pós-login (3s invisível), o ENTITY_SPAWN de B não
        sai mais NO login (broadcast filtrado por _can_see) — ele chega
        quando a visibilidade é restaurada, via delta visibility_changed no
        tick seguinte. fake_login já expira a imunidade (clear_login_immunity,
        que também anuncia em _visibility_changed_this_tick); só falta rodar
        o tick que despacha."""
        session_a, fw_a = await fake_login(self.mgr, "s1", "user_spa", 115, 389)
        fw_a.sent.clear()   # limpa mensagens do próprio login de A
        session_b, _ = await fake_login(self.mgr, "s2", "user_spb", 117, 389)
        for _ in range(3):
            self.ws_server._tick(0.05)
            await asyncio.sleep(0)   # processa o create_task do dispatch
        # Aceita os dois canais: ENTITY_SPAWN avulso OU AOI_UPDATE.spawned
        # (o caminho atual entrega via AOI_UPDATE).
        b_eid = session_b.entity_id
        spawn_eids = [s.get("eid") for s in get_msgs_of_type(fw_a, MsgType.ENTITY_SPAWN)]
        for mt, payload, _, _ in get_messages(fw_a):
            if mt == MsgType.AOI_UPDATE:
                spawn_eids += [s.get("eid") for s in payload.get("spawned", [])]
        self.assertIn(b_eid, spawn_eids,
                      "A não recebeu spawn de B (nem ENTITY_SPAWN nem AOI_UPDATE.spawned)")


# ─────────────────────────────────────────────────────────────────────────────
# 3. AOI_UPDATE distribution
# ─────────────────────────────────────────────────────────────────────────────

class TestAOIUpdate(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        await fake_login(self.mgr, "seed", "seed_aoi_upd", 130, 374)
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_ticks(self.ws_server, 50))
        await self.mgr.on_disconnect("seed")

    async def _run_ticks_async(self, n: int):
        """Roda ticks do servidor processando tasks assíncronas."""
        for _ in range(n):
            self.ws_server._tick(0.05)
            await asyncio.sleep(0)   # yield para processar create_task

    async def test_mob_combat_result_sent_to_both_players(self):
        """COMBAT_RESULT de mob deve chegar aos dois players no AOI."""
        from engine.components import CombatState, CombatStats, TileMovement
        session_a, fw_a = await fake_login(self.mgr, "s1", "cr_a", 115, 389)
        session_b, fw_b = await fake_login(self.mgr, "s2", "cr_b", 117, 389)

        mob_eid = first_mob(self.ws_server)
        if not mob_eid:
            self.skipTest("Sem mobs")

        player_eid = session_a.entity_id
        ptm = self.ws_server.world.get_component(player_eid, TileMovement)
        # set_entity_tile sincroniza TileMovement E Position (pixel) — só
        # mexer em current_tile_x/y (como antes) deixava o mob fisicamente
        # longe (Position ainda na zona de spawn real dele), e
        # _select_target (Sistema de Facções, Fase 5 — agora também
        # considera NPCs de combate como candidato) podia escolher um NPC
        # de combate mais perto em pixel real do que o player "teleportado"
        # só por tile, mesmo intenção do teste sendo atacar o player.
        set_entity_tile(self.ws_server, mob_eid, ptm.current_tile_x + 1, ptm.current_tile_y)
        mob_cs_state = self.ws_server.world.get_component(mob_eid, CombatState)
        mob_cs_state.target_entity_id = player_eid
        self.ws_server._attack_timers[f"mob_{mob_eid}"] = 0.0

        fw_a.sent.clear()
        fw_b.sent.clear()
        await self._run_ticks_async(5)

        # Verifica AOI_UPDATE com combat em ambos
        def has_combat_result(fw):
            for mt, payload, _, _ in get_messages(fw):
                if mt == MsgType.AOI_UPDATE and payload.get("combat"):
                    return True
            return False

        self.assertTrue(has_combat_result(fw_a), "Player A não recebeu COMBAT_RESULT")
        self.assertTrue(has_combat_result(fw_b), "Player B não recebeu COMBAT_RESULT")

    async def test_mob_despawn_sent_to_both_players(self):
        """Quando mob morre, ENTITY_DESPAWN deve chegar aos dois players."""
        from engine.components import CombatState, CombatStats, TileMovement
        # Login próximo à zona para garantir mobs no WORLD_STATE (known_eids)
        session_a, fw_a = await fake_login(self.mgr, "s1", "dp_a", 128, 374)
        session_b, fw_b = await fake_login(self.mgr, "s2", "dp_b", 132, 374)

        mob_eid = first_mob(self.ws_server)
        if not mob_eid:
            self.skipTest("Sem mobs")

        mob_cs = self.ws_server.world.get_component(mob_eid, CombatStats)
        player_eid = session_a.entity_id
        mob_tm = self.ws_server.world.get_component(mob_eid, TileMovement)
        _from_tx, _from_ty = mob_tm.current_tile_x, mob_tm.current_tile_y
        # teleport_mob_to_player sincroniza TileMovement + Position (pixel) +
        # InitialPosition + reseta a IA — mexer só em current_tile_x/y (como
        # antes) deixava o mob "fisicamente" na zona de spawn original: a
        # aquisição de alvo (pixel, limitada ao raio de aggro — §34.10) não
        # achava ninguém, o mob entrava em RETURNING (modo evasão = IMUNE) e
        # o ataque do player nunca o matava — despawn nunca acontecia.
        from tests.helpers import teleport_mob_to_player
        teleport_mob_to_player(self.ws_server, mob_eid, player_eid)
        # Teleporte manual não gera evento de movimento — sem registrar em
        # _moved_this_tick, o AOI nunca detecta o mob entrando no raio dos
        # players, ele nunca entra em known_eids, e o despawn da morte é
        # filtrado (só se despawna o que o cliente CONHECE). Registra o
        # deslocamento como um move real e dá uns ticks pro spawn chegar.
        self.ws_server._moved_this_tick.append({
            "eid": mob_eid,
            "tx": mob_tm.current_tile_x, "ty": mob_tm.current_tile_y,
            "from_tx": _from_tx, "from_ty": _from_ty,
        })
        await self._run_ticks_async(3)
        self.assertIn(mob_eid, session_a.known_eids,
                      "mob não entrou em known_eids de A após mover pro AOI")

        mob_cs.current_hp = 1
        self.ws_server.set_player_target("s1", mob_eid)
        fw_a.sent.clear()
        fw_b.sent.clear()

        await self._run_ticks_async(60)

        def has_despawn(fw, eid):
            for mt, payload, _, _ in get_messages(fw):
                if mt == MsgType.AOI_UPDATE:
                    if eid in payload.get("despawned", []):
                        return True
            return False

        self.assertTrue(has_despawn(fw_a, mob_eid), "Player A não recebeu ENTITY_DESPAWN")
        self.assertTrue(has_despawn(fw_b, mob_eid), "Player B não recebeu ENTITY_DESPAWN")

    async def test_player_movement_sent_to_nearby_player(self):
        """Quando A se move, B deve receber ENTITY_MOVE de A."""
        from shared.messages import encode
        session_a, fw_a = await fake_login(self.mgr, "s1", "mv_a", 115, 389)
        session_b, fw_b = await fake_login(self.mgr, "s2", "mv_b", 117, 389)

        fw_b.sent.clear()
        move_msg = encode(MsgType.MOVE, {"tx": 116, "ty": 389, "from_tx": 115, "from_ty": 389})
        await self.mgr.on_message(session_a, move_msg)

        moves_b = get_msgs_of_type(fw_b, MsgType.ENTITY_MOVE)
        a_moves = [m for m in moves_b if m.get("eid") == session_a.entity_id]
        self.assertGreater(len(a_moves), 0,
                           "B não recebeu ENTITY_MOVE quando A se moveu")

    async def test_distant_player_does_not_receive_movement(self):
        """Player distante não deve receber movimentos fora do AOI."""
        from shared.messages import encode
        session_a, fw_a = await fake_login(self.mgr, "s1", "dmv_a", 115, 389)
        session_b, fw_b = await fake_login(self.mgr, "s2", "dmv_b", 10, 10)

        fw_b.sent.clear()
        move_msg = encode(MsgType.MOVE, {"tx": 116, "ty": 389, "from_tx": 115, "from_ty": 389})
        await self.mgr.on_message(session_a, move_msg)

        moves_b = get_msgs_of_type(fw_b, MsgType.ENTITY_MOVE)
        a_moves = [m for m in moves_b if m.get("eid") == session_a.entity_id]
        self.assertEqual(len(a_moves), 0,
                         "B em (10,10) recebeu movimento de A em (115,389)")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Disconnect
# ─────────────────────────────────────────────────────────────────────────────

class TestDisconnect(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    async def test_disconnect_removes_session(self):
        """Após disconnect, sessão deve ser removida do manager."""
        session, _ = await fake_login(self.mgr, "s1", "user_disc")
        await self.mgr.on_disconnect("s1")
        self.assertNotIn("s1", self.mgr._sessions)

    async def test_disconnect_removes_player_from_ecs(self):
        """Após disconnect, entidade do player deve ser removida do ECS."""
        from engine.components import TileMovement
        session, _ = await fake_login(self.mgr, "s1", "user_disc_ecs")
        player_eid = session.entity_id
        await self.mgr.on_disconnect("s1")
        tm = self.ws_server.world.get_component(player_eid, TileMovement)
        self.assertIsNone(tm, "Componente TileMovement ainda existe após disconnect")

    async def test_disconnect_sends_entity_despawn_to_other_player(self):
        """Quando A desconecta, B deve receber ENTITY_DESPAWN de A."""
        session_a, fw_a = await fake_login(self.mgr, "s1", "user_da", 115, 389)
        session_b, fw_b = await fake_login(self.mgr, "s2", "user_db", 117, 389)

        fw_b.sent.clear()
        await self.mgr.on_disconnect("s1")

        despawns = get_msgs_of_type(fw_b, MsgType.ENTITY_DESPAWN)
        da = [d for d in despawns if d.get("eid") == session_a.entity_id]
        self.assertGreater(len(da), 0, "B não recebeu ENTITY_DESPAWN quando A desconectou")

    async def test_disconnect_removes_from_known_eids_of_others(self):
        """Quando A desconecta, eid de A deve sair de known_eids de B."""
        session_a, _ = await fake_login(self.mgr, "s1", "user_kd_a", 115, 389)
        session_b, _ = await fake_login(self.mgr, "s2", "user_kd_b", 117, 389)

        self.assertIn(session_a.entity_id, session_b.known_eids,
                      "Pré-condição: A deveria estar em known_eids de B")

        await self.mgr.on_disconnect("s1")

        self.assertNotIn(session_a.entity_id, session_b.known_eids,
                         "Eid de A ainda em known_eids de B após disconnect")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Player death
# ─────────────────────────────────────────────────────────────────────────────

class TestPlayerDeathEvent(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        await fake_login(self.mgr, "seed", "seed_pdeath", 130, 374)
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_ticks(self.ws_server, 50))
        await self.mgr.on_disconnect("seed")

    async def _run_ticks_async(self, n: int):
        for _ in range(n):
            self.ws_server._tick(0.05)
            await asyncio.sleep(0)

    async def test_player_death_sends_player_death_message(self):
        """Quando servidor detecta HP=0, cliente deve receber PLAYER_DEATH."""
        session, fw = await fake_login(self.mgr, "s1", "user_pdeath", 130, 374)
        player_eid = session.entity_id

        fw.sent.clear()
        # Chama _handle_player_death diretamente (não depende de EnemyAI)
        self.ws_server._handle_player_death(player_eid)
        # O método coloca em _player_deaths_this_tick; processa via tick
        await self._run_ticks_async(2)

        deaths = get_msgs_of_type(fw, MsgType.PLAYER_DEATH)
        self.assertGreater(len(deaths), 0, "Cliente não recebeu PLAYER_DEATH")

    async def test_player_corpse_stays_dead_until_revive(self):
        """Após morte, corpo fica com HP=0/GhostState.is_dead até liberar espírito
        e reviver (fluxo de ghost/cemitério substitui o respawn instantâneo)."""
        from engine.components import CombatStats, CombatState, TileMovement, GhostState
        session, fw = await fake_login(self.mgr, "s1", "user_hpreset", 130, 374)

        player_eid = session.entity_id
        mob_eid = first_mob(self.ws_server)
        if not mob_eid:
            self.skipTest("Sem mobs")

        pcs = self.ws_server.world.get_component(player_eid, CombatStats)
        hp_max = pcs.max_hp
        pcs.current_hp = 1
        # teleport_mob_to_player sincroniza tile + pixel + InitialPosition —
        # mexer só nos tiles deixava o mob "fisicamente" longe: a aquisição
        # de alvo (pixel, limitada ao raio de aggro — §34.10) não achava o
        # player, o mob entrava em RETURNING e nunca atacava (player nunca
        # morria). Mesma classe de fragilidade do test_mob_despawn acima.
        from tests.helpers import teleport_mob_to_player
        teleport_mob_to_player(self.ws_server, mob_eid, player_eid)
        mob_cs = self.ws_server.world.get_component(mob_eid, CombatState)
        mob_cs.target_entity_id = player_eid
        self.ws_server._attack_timers[f"mob_{mob_eid}"] = 0.0

        # 240 ticks (12s) — margem contra miss/dodge/parry do mob (só 5
        # ticks/0.25s dava 1 tentativa só; random é global/compartilhado
        # entre testes do processo inteiro — mesma classe de flakiness já
        # documentada em TestPlayerAttacksMob/TestAutoAttackFlow,
        # tests/test_combat.py — exposta aqui por reordenação de testes
        # em runs completos da suíte, não por bug de verdade).
        await self._run_ticks_async(240)

        pcs_after = self.ws_server.world.get_component(player_eid, CombatStats)
        gst_after = self.ws_server.world.get_component(player_eid, GhostState)
        self.assertLessEqual(pcs_after.current_hp, 0,
                              "Corpo não deveria ter HP restaurado antes do revive")
        self.assertTrue(gst_after.is_dead, "GhostState.is_dead deveria ser True após morte")

        # Libera espírito e revive no cemitério (hp_frac=1.0)
        self.ws_server._handle_release_spirit(player_eid)
        self.assertTrue(gst_after.is_ghost)
        self.ws_server._revive_player(player_eid, hp_frac=1.0, at_corpse=False)

        pcs_revived = self.ws_server.world.get_component(player_eid, CombatStats)
        self.assertEqual(pcs_revived.current_hp, hp_max,
                         "HP do player não foi restaurado após revive no cemitério")
        self.assertFalse(gst_after.is_dead)
        self.assertFalse(gst_after.is_ghost)

    async def test_marcador_sintetico_de_corpo_so_aparece_apos_liberar_espirito(self):
        """Bug real relatado pelo usuário 22/07/2026: corpo duplicava ao
        morrer (a entidade real já tingida de cadáver + o marcador
        sintético `player_corpse`, entregue cedo demais pelo sweep de AOI)
        e um dos dois sumia ao liberar o espírito. O marcador só deve
        entrar no AOI de outra sessão DEPOIS que GhostState.is_ghost=True."""
        victim, _fw_v = await fake_login(self.mgr, "corpse_v", "corpseuservit", 130, 374)
        observer, fw_o = await fake_login(self.mgr, "corpse_o", "corpseuserobs", 130, 374)
        victim_eid = victim.entity_id

        self.ws_server._handle_player_death(victim_eid)
        self.assertIn(victim_eid, self.ws_server._player_corpses)

        update_before = self.mgr._build_update_for_session(observer, {}, 130, 374)
        spawned_before = update_before.get("spawned", [])
        self.assertFalse(
            any(s.get("kind") == "player_corpse" for s in spawned_before),
            "marcador sintético de corpo não deveria aparecer antes de liberar o espírito")

        self.ws_server._handle_release_spirit(victim_eid)

        update_after = self.mgr._build_update_for_session(observer, {}, 130, 374)
        spawned_after = update_after.get("spawned", [])
        self.assertTrue(
            any(s.get("kind") == "player_corpse" for s in spawned_after),
            "marcador sintético de corpo deveria aparecer depois de liberar o espírito")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Loot em grupo — LOOT_UPDATE sincroniza quem não clicou
# ─────────────────────────────────────────────────────────────────────────────

class TestPartyLootSync(unittest.IsolatedAsyncioTestCase):
    """Bug real relatado pelo usuário 17/07/2026: quando A sacava algo de
    um corpse compartilhado do grupo, B (que também tinha o corpse
    aberto, via LOOT_AVAILABLE) nunca ficava sabendo — via ouro/item
    "fantasma" já pego, clicar nele voltava vazio sem nunca corrigir a
    cópia local, e o corpo "bugava e fechava". Fix: LOOT_UPDATE avisa o
    resto do grupo (exceto quem sacou) toda vez que algo sai do corpse."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    def _make_corpse(self, owner_eid: int, coins: int = 11, items: list = None) -> int:
        cid = self.ws_server._next_corpse_id
        self.ws_server._next_corpse_id += 1
        self.ws_server._corpses[cid] = {
            "tx": 130, "ty": 374, "owner_eid": owner_eid,
            "items": items or [], "coins": coins,
            "timer": 120.0, "map": self.ws_server._map_file,
        }
        return cid

    async def test_loot_update_avisa_resto_do_grupo(self):
        from shared.messages import encode
        session_a, fw_a = await fake_login(self.mgr, "s1", "user_loot_a", 130, 374)
        session_b, fw_b = await fake_login(self.mgr, "s2", "user_loot_b", 131, 374)
        self.assertIsNone(self.ws_server.request_party_invite(
            session_a.entity_id, session_b.entity_id))
        self.ws_server.respond_party_invite(session_b.entity_id, accept=True)
        cid = self._make_corpse(owner_eid=session_a.entity_id)

        fw_b.sent.clear()
        await self.mgr.on_message(session_a, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "gold"}))

        updates = get_msgs_of_type(fw_b, MsgType.LOOT_UPDATE)
        self.assertEqual(len(updates), 1,
                         "B deveria receber LOOT_UPDATE quando A sacou o ouro do corpse compartilhado")
        self.assertEqual(updates[0]["corpse_id"], cid)
        self.assertEqual(updates[0]["coins_taken"], 11)
        self.assertEqual(updates[0]["item_names_taken"], [])

    async def test_loot_update_nao_volta_pro_proprio_requester(self):
        from shared.messages import encode
        session_a, fw_a = await fake_login(self.mgr, "s1", "user_loot_c", 130, 374)
        session_b, _    = await fake_login(self.mgr, "s2", "user_loot_d", 131, 374)
        self.assertIsNone(self.ws_server.request_party_invite(
            session_a.entity_id, session_b.entity_id))
        self.ws_server.respond_party_invite(session_b.entity_id, accept=True)
        cid = self._make_corpse(owner_eid=session_a.entity_id)

        fw_a.sent.clear()
        await self.mgr.on_message(session_a, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "gold"}))

        self.assertEqual(len(get_msgs_of_type(fw_a, MsgType.LOOT_UPDATE)), 0,
                         "quem sacou já sabe via LOOT_RESULT — não deveria receber LOOT_UPDATE também")
        self.assertEqual(len(get_msgs_of_type(fw_a, MsgType.LOOT_RESULT)), 1)

    async def test_sem_grupo_nao_manda_loot_update_pra_ninguem(self):
        from shared.messages import encode
        session_a, fw_a = await fake_login(self.mgr, "s1", "user_loot_solo", 130, 374)
        cid = self._make_corpse(owner_eid=session_a.entity_id)

        fw_a.sent.clear()
        await self.mgr.on_message(session_a, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "gold"}))

        self.assertEqual(len(get_msgs_of_type(fw_a, MsgType.LOOT_UPDATE)), 0)
        self.assertEqual(len(get_msgs_of_type(fw_a, MsgType.LOOT_RESULT)), 1)


class TestHarvestableEmptyNaoDisparaDespawnGenerico(unittest.IsolatedAsyncioTestCase):
    """Bug real relatado pelo usuário 25/07/2026: esvaziar uma caixa de
    harvestable removia a entidade da tela de QUALQUER jogador no AOI
    (não só de quem saqueou). Causa: `_handle_loot_request` manda um
    ENTITY_DESPAWN genérico (eid negativo) sempre que um corpse fica
    REALMENTE vazio — comportamento certo pra corpse de mob morto
    (deveria mesmo sumir), mas o código nunca checava se o corpse era
    um harvestable (permanente, `no_decay=True`, com seu PRÓPRIO
    mecanismo de "fica visível vazio até reabastecer" — Fase M2). Ao
    reabastecer depois, o cliente já tinha perdido a entrada em
    `_available_loot` (removida junto com o despawn indevido), caindo
    no fallback antigo de `create_corpse` — desenhando a elipse velha
    em cima do que deveria voltar a ser a caixa."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    def _make_corpse(self, owner_eid: int, no_decay: bool = False,
                     coins: int = 10, items: list = None) -> int:
        cid = self.ws_server._next_corpse_id
        self.ws_server._next_corpse_id += 1
        self.ws_server._corpses[cid] = {
            "tx": 130, "ty": 374, "owner_eid": owner_eid,
            "items": items or [], "coins": coins,
            "timer": 120.0, "map": self.ws_server._map_file,
            "no_decay": no_decay,
        }
        return cid

    async def test_harvestable_esvaziado_nao_manda_entity_despawn(self):
        from shared.messages import encode
        session, fw = await fake_login(self.mgr, "s1", "user_hv_desp_a", 130, 374)
        cid = self._make_corpse(owner_eid=-1, no_decay=True)

        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "all"}))

        despawns = get_msgs_of_type(fw, MsgType.ENTITY_DESPAWN)
        self.assertEqual(despawns, [],
                         "harvestable esvaziado nunca deveria disparar ENTITY_DESPAWN")

    async def test_corpse_de_mob_esvaziado_continua_mandando_entity_despawn(self):
        """Regressão: corpse de mob morto (sem no_decay) precisa
        continuar sumindo da tela quando esvazia — comportamento de
        17/07/2026 intacto."""
        from shared.messages import encode
        session, fw = await fake_login(self.mgr, "s1", "user_hv_desp_b", 130, 374)
        cid = self._make_corpse(owner_eid=session.entity_id, no_decay=False)

        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "all"}))

        despawns = get_msgs_of_type(fw, MsgType.ENTITY_DESPAWN)
        self.assertEqual(len(despawns), 1)
        self.assertEqual(despawns[0]["eid"], -cid)

    async def test_corpse_com_item_pessoal_pendente_nao_manda_despawn_ao_esvaziar_pote_comum(self):
        """Bug real relatado pelo usuário 28/07/2026: arqueiro com o
        talento Reciclagem recebe flecha no pote COMUM do corpse; com a
        quest "Veneno Mortal" ativa, o mesmo corpse (aranha) também tem
        "Veneno de Aranha" no pote PESSOAL (quest_rolls, resolvido por
        jogador — Fase L1). Saquear só a flecha esvaziava o pote comum
        e `_still_has_loot` (que só olhava items/coins) declarava o
        corpo "vazio" — despawn genérico removia a entidade da AOI de
        todo mundo com o item da quest ainda intocado no pote pessoal,
        nunca mais lootável."""
        from shared.messages import encode
        session, fw = await fake_login(self.mgr, "s1", "user_hv_desp_e", 130, 374)
        cid = self._make_corpse(owner_eid=session.entity_id, no_decay=False,
                                coins=0, items=[{"name": "Flecha", "stack": 3}])
        # Simula o pote pessoal já resolvido (LOOT_AVAILABLE original) —
        # request_loot() não re-sorteia pra quem já está em quest_rolls.
        self.ws_server._corpses[cid]["quest_rolls"] = {
            session.entity_id: [{"name": "Veneno de Aranha", "stack": 1}],
        }

        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST,
            {"corpse_id": cid, "take": "item", "item_name": "Flecha"}))

        despawns = get_msgs_of_type(fw, MsgType.ENTITY_DESPAWN)
        self.assertEqual(despawns, [],
                         "corpo com item pessoal de quest pendente não deveria sumir")

    async def test_corpse_sem_item_pessoal_pendente_manda_despawn_ao_esvaziar_pote_comum(self):
        """Regressão: sem nada pendente em quest_rolls, esvaziar o pote
        comum continua disparando o despawn normalmente."""
        from shared.messages import encode
        session, fw = await fake_login(self.mgr, "s1", "user_hv_desp_f", 130, 374)
        cid = self._make_corpse(owner_eid=session.entity_id, no_decay=False,
                                coins=0, items=[{"name": "Flecha", "stack": 3}])

        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST,
            {"corpse_id": cid, "take": "item", "item_name": "Flecha"}))

        despawns = get_msgs_of_type(fw, MsgType.ENTITY_DESPAWN)
        self.assertEqual(len(despawns), 1)
        self.assertEqual(despawns[0]["eid"], -cid)


# ─────────────────────────────────────────────────────────────────────────────
# 6b. Loot condicional de quest é resolvido POR JOGADOR (Fase L1, 25/07/2026)
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionalLootPerPlayer(unittest.IsolatedAsyncioTestCase):
    """Bug real confirmado (usuário perguntou, investigação achou): antes,
    o loot condicional de quest (ex.: Pelo de Urso) era decidido 1x na
    morte do mob contra a QuestLog do first-attacker e ficava FIXO no
    corpse — qualquer membro do MESMO GRUPO que saqueasse depois via
    request_loot() (regra "free-for-all dentro do grupo") via/pegava o
    item mesmo sem a quest. Fix: cada jogador tem seu próprio sorteio,
    resolvido na hora que ELE interage com o corpse, cacheado — nunca
    re-sorteado pro mesmo jogador."""

    QID = "qcl_teste"

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
        QUESTS[self.QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="collect_item", target="Urso",
                                     loot_item="Pelo de Urso", count=1, loot_chance=1.0),),
            reward=QuestReward(xp=1),
        )

    async def asyncTearDown(self):
        from content.quests_data import QUESTS
        QUESTS.pop(self.QID, None)

    def _make_corpse(self, owner_eid: int, mob_name: str = "Urso") -> int:
        cid = self.ws_server._next_corpse_id
        self.ws_server._next_corpse_id += 1
        self.ws_server._corpses[cid] = {
            "tx": 130, "ty": 374, "owner_eid": owner_eid,
            "items": [{"name": "Item Comum", "icon_key": "", "item_type": "material",
                       "rarity": "common", "value": 1, "slot": "", "stack": 1}],
            "coins": 0, "timer": 120.0, "map": self.ws_server._map_file,
            "mob_name": mob_name, "mob_race": "Urso", "quest_rolls": {},
        }
        return cid

    async def test_jogador_com_quest_recebe_item_condicional(self):
        from engine.components import QuestLog
        session, _ = await fake_login(self.mgr, "s1", "user_ql_a", 130, 374)
        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]
        cid = self._make_corpse(owner_eid=session.entity_id)
        corpse = self.ws_server._corpses[cid]

        extra = self.ws_server._resolve_conditional_loot_for(corpse, session.entity_id)
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0]["name"], "Pelo de Urso")

    async def test_jogador_sem_quest_nao_recebe_nada(self):
        session, _ = await fake_login(self.mgr, "s1", "user_ql_b", 130, 374)
        cid = self._make_corpse(owner_eid=session.entity_id)
        corpse = self.ws_server._corpses[cid]

        extra = self.ws_server._resolve_conditional_loot_for(corpse, session.entity_id)
        self.assertEqual(extra, [])

    async def test_dois_jogadores_do_mesmo_grupo_veem_coisas_diferentes(self):
        """O cenário exato da dúvida do usuário: A (com a quest) e B (sem)
        no MESMO grupo, saqueando o MESMO corpse — A deveria ver o item
        condicional, B não, mesmo sendo o mesmo objeto de corpse."""
        from engine.components import QuestLog
        session_a, _ = await fake_login(self.mgr, "s1", "user_ql_c", 130, 374)
        session_b, _ = await fake_login(self.mgr, "s2", "user_ql_d", 131, 374)
        self.assertIsNone(self.ws_server.request_party_invite(
            session_a.entity_id, session_b.entity_id))
        self.ws_server.respond_party_invite(session_b.entity_id, accept=True)

        ql_a = self.ws_server.world.get_component(session_a.entity_id, QuestLog)
        ql_a.active[self.QID] = [0]
        # session_b nunca aceitou a quest — QuestLog dela fica sem o objetivo.

        cid = self._make_corpse(owner_eid=session_a.entity_id)
        corpse = self.ws_server._corpses[cid]

        extra_a = self.ws_server._resolve_conditional_loot_for(corpse, session_a.entity_id)
        extra_b = self.ws_server._resolve_conditional_loot_for(corpse, session_b.entity_id)
        self.assertEqual([it["name"] for it in extra_a], ["Pelo de Urso"])
        self.assertEqual(extra_b, [])

    async def test_reabrir_nao_re_sorteia_pro_mesmo_jogador(self):
        """Uma vez decidido (mesmo se o resultado for [], sem a quest), o
        MESMO jogador nunca recebe um sorteio novo pro mesmo corpse."""
        session, _ = await fake_login(self.mgr, "s1", "user_ql_e", 130, 374)
        from engine.components import QuestLog
        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]
        cid = self._make_corpse(owner_eid=session.entity_id)
        corpse = self.ws_server._corpses[cid]

        first  = self.ws_server._resolve_conditional_loot_for(corpse, session.entity_id)
        # Remove a quest ANTES da 2a chamada — se re-sorteasse, o resultado
        # mudaria (a quest não está mais ativa); como está cacheado, não muda.
        del ql.active[self.QID]
        second = self.ws_server._resolve_conditional_loot_for(corpse, session.entity_id)
        self.assertEqual(first, second)
        self.assertEqual([it["name"] for it in second], ["Pelo de Urso"])

    async def test_take_all_credita_o_item_condicional_via_loot_request(self):
        """Fluxo ponta a ponta: LOOT_REQUEST(take="all") do jogador com a
        quest devolve o item comum E o condicional; retirar de novo (take
        "item" pelo nome) não duplica."""
        from shared.messages import encode
        from engine.components import QuestLog
        session, fw = await fake_login(self.mgr, "s1", "user_ql_f", 130, 374)
        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]
        cid = self._make_corpse(owner_eid=session.entity_id)

        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "all"}))

        results = get_msgs_of_type(fw, MsgType.LOOT_RESULT)
        self.assertEqual(len(results), 1)
        names = {it["name"] for it in results[0]["items"]}
        self.assertEqual(names, {"Item Comum", "Pelo de Urso"})

        # Corpse já vazio — pedir de novo não devolve nada (nem duplica).
        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "all"}))
        results2 = get_msgs_of_type(fw, MsgType.LOOT_RESULT)
        self.assertEqual(results2[0]["items"], [])


class TestHarvestableM1(unittest.IsolatedAsyncioTestCase):
    """Item de mapa saqueável (Fase M1, revisão 2, 25/07/2026) — entidade
    real (posição+aparência+loot, SEM combate/diálogo), colisão e Y-sort
    automáticos via os mesmos sistemas genéricos que já servem NPC/mob
    (TileValidationSystem/RenderSystem) — sem gravar nada no object_matrix
    do mapa. Loot em si (self._corpses, público/no_decay/condicional por
    jogador) não mudou — só a camada de descoberta/visual/colisão."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    def _make_harvestable(self, tx: int, ty: int, map_file: str = None,
                          name: str = "Arbusto", sprite: str = "pr_box1",
                          items: list = None, coins: int = 0,
                          respawn_s: float = 0) -> int:
        """Cria o harvestable pelo caminho de produção real
        (_create_harvestables_for_map) — devolve o corpse_id (hid).
        Em produção, `_load_map_for` chama isso ANTES de um snapshot-diff
        que tagueia MapLocation em toda entidade nova (ver
        server/world_server.py::_load_map_for) — como o teste chama
        _create_harvestables_for_map direto (sem passar por
        _load_map_for), replica esse passo aqui, senão a entidade nunca
        aparece no AOI (in_aoi exige MapLocation pra eid>=0)."""
        map_key = map_file or self.ws_server._map_file
        spawn_points = {"harvestables": [{
            "x": tx, "y": ty, "name": name, "sprite": sprite,
            "items": items if items is not None else ["training_sword"],
            "coins": coins, "respawn_s": respawn_s,
        }]}
        before_corpses = set(self.ws_server._corpses.keys())
        before_hv_eids = set(self.ws_server._harvestable_eids)
        self.ws_server._create_harvestables_for_map(spawn_points, map_key)
        from engine.components import MapLocation as _MLhv
        for _new_eid in set(self.ws_server._harvestable_eids) - before_hv_eids:
            if self.ws_server.world.get_component(_new_eid, _MLhv) is None:
                self.ws_server.world.add_component(_new_eid, _MLhv(map_key))
        return (set(self.ws_server._corpses.keys()) - before_corpses).pop()

    def _harvestable_eid_for(self, hid: int) -> int:
        from engine.components import Harvestable
        for eid in self.ws_server._harvestable_eids:
            hv = self.ws_server.world.get_component(eid, Harvestable)
            if hv and hv.corpse_id == hid:
                return eid
        raise AssertionError(f"nenhuma entidade harvestable achada pro corpse_id={hid}")

    def test_create_harvestables_for_map_resolve_items_e_ignora_invalido(self):
        spawn_points = {"harvestables": [{
            "x": 50, "y": 60, "name": "Arbusto de Frutas", "coins": 3,
            "sprite": "pr_box1",
            "items": ["training_sword", ("small_hp_potion", 3),
                      "item_key_que_nao_existe"],
        }]}
        before_corpses = set(self.ws_server._corpses.keys())
        before_hv_eids = set(self.ws_server._harvestable_eids)
        self.ws_server._create_harvestables_for_map(spawn_points, "map_test")
        new_ids = set(self.ws_server._corpses.keys()) - before_corpses
        self.assertEqual(len(new_ids), 1)
        hid = new_ids.pop()
        corpse = self.ws_server._corpses[hid]

        self.assertEqual(corpse["owner_eid"], -1)
        self.assertTrue(corpse["no_decay"])
        self.assertEqual(corpse["timer"], float("inf"))
        self.assertEqual(corpse["map"], "map_test")
        self.assertEqual(corpse["tx"], 50)
        self.assertEqual(corpse["ty"], 60)
        self.assertEqual(corpse["name"], "Arbusto de Frutas")
        self.assertEqual(corpse["coins"], 3)
        self.assertNotIn("color", corpse)      # não existe mais (Renderable cuida disso)
        self.assertNotIn("sprite_id", corpse)  # idem
        names = [it["name"] for it in corpse["items"]]
        self.assertEqual(len(names), 2)  # item inválido foi ignorado
        self.assertIn("Espada de treinamento", names)
        potion = next(it for it in corpse["items"]
                     if it["name"] != "Espada de treinamento")
        self.assertEqual(potion["stack"], 3)

        # Entidade real criada junto (Fase M1, revisão 2)
        new_hv_eids = set(self.ws_server._harvestable_eids) - before_hv_eids
        self.assertEqual(len(new_hv_eids), 1)
        hv_eid = new_hv_eids.pop()
        from engine.components import Harvestable, Renderable, TileMovement, Position
        hv  = self.ws_server.world.get_component(hv_eid, Harvestable)
        ren = self.ws_server.world.get_component(hv_eid, Renderable)
        tm  = self.ws_server.world.get_component(hv_eid, TileMovement)
        pos = self.ws_server.world.get_component(hv_eid, Position)
        self.assertEqual(hv.corpse_id, hid)
        self.assertEqual(ren.sprite_id, "pr_box1")
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (50, 60))
        self.assertFalse(tm.is_moving)
        self.assertIsNotNone(pos)

    def test_sprite_id_resolve_via_tile_sprites_catalog(self):
        """sprite_id precisa ser um ID já catalogado em engine/tileset.py
        (OBJECT_SHEET_TILE_MAP/SHEET_TILE_MAP) — TILE_SPRITES.get_raw_sprite
        é o MESMO mecanismo usado pra sprites de objeto de mapa (árvore,
        caixa etc.), reaproveitado aqui em vez de um ícone quadrado avulso."""
        import pygame
        if not pygame.display.get_surface():
            pygame.display.set_mode((1, 1))
        from ui.tile_sprite_manager import TILE_SPRITES
        surf = TILE_SPRITES.get_raw_sprite("pr_box1")
        self.assertIsNotNone(surf)
        self.assertEqual(surf.get_size(), (32, 64))

    def test_merge_entities_json_parses_harvestables(self):
        import json, tempfile, os as _os
        from engine.map_loader import _merge_entities_json

        spawn_points = {
            "player": (0, 0), "enemies": [], "portals": [],
            "merchants": [], "quest_givers": [], "blacksmiths": [],
            "trainers": [], "spawn_zones": [], "transitions": [],
            "ambient_zones": [], "pvp_zones": [], "default_ambient": "",
            "training_dummies": [], "combat_npcs": [],
        }
        data = {"harvestables": [{
            "x": 10, "y": 20, "name": "Planta", "coins": 5,
            "sprite": "pr_box1",
            "items": ["training_sword", ["small_hp_potion", 2]],
        }]}
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            _merge_entities_json(path, spawn_points)
        finally:
            _os.remove(path)

        hv = spawn_points["harvestables"]
        self.assertEqual(len(hv), 1)
        self.assertEqual(hv[0]["x"], 10)
        self.assertEqual(hv[0]["y"], 20)
        self.assertEqual(hv[0]["name"], "Planta")
        self.assertEqual(hv[0]["coins"], 5)
        self.assertEqual(hv[0]["sprite"], "pr_box1")
        self.assertNotIn("color", hv[0])
        # Lista JSON vira tuple (normalize_reward_entry só reconhece tuple);
        # string crua permanece string.
        self.assertEqual(hv[0]["items"][0], "training_sword")
        self.assertEqual(hv[0]["items"][1], ("small_hp_potion", 2))
        self.assertIsInstance(hv[0]["items"][1], tuple)

    async def test_harvestable_tem_colisao_real(self):
        """Antes (dict solto em _corpses): jogador atravessava por cima.
        Agora: TileMovement da entidade real é pego pelo cache de tiles
        ocupados de TileValidationSystem (nenhum código dedicado — mesmo
        mecanismo que já bloqueia tile de mob/NPC). Precisa ser async:
        _tick() aciona _on_tick -> asyncio.create_task, que exige um
        event loop rodando (só os testes async desta classe têm um)."""
        from engine.world_systems import is_tile_walkable
        from tests.helpers import spawn_player
        player_eid = spawn_player(self.ws_server, "s1", 138, 380)
        self.ws_server._tick(0.05)
        # Antes de criar o harvestable, o tile precisa estar livre (senão
        # o teste provaria só que o TERRENO já bloqueava, não a entidade).
        self.assertTrue(is_tile_walkable(player_eid, 140, 380))
        self._make_harvestable(140, 380)
        self.ws_server._tick(0.05)
        self.assertFalse(is_tile_walkable(player_eid, 140, 380))

    async def test_harvestable_com_sprite_passavel_no_catalogo_nao_trava_o_tile(self):
        """Bug real relatado pelo usuário 25/07/2026: "pl_vomito" é
        PASSÁVEL no catálogo (OBJECT_MAPPING['pl_vomito'].is_solid ==
        False), mas travava o tile de qualquer jeito, porque
        create_harvestable_entity sempre adicionava TileMovement sem
        olhar a config real do sprite. Fix: Harvestable.solid vem do
        catálogo; TileValidationSystem só marca o tile ocupado se solid."""
        from engine.world_systems import is_tile_walkable
        from tests.helpers import spawn_player
        player_eid = spawn_player(self.ws_server, "s1", 138, 380)
        self.ws_server._tick(0.05)
        self.assertTrue(is_tile_walkable(player_eid, 140, 380))
        self._make_harvestable(140, 380, sprite="pl_vomito")
        self.ws_server._tick(0.05)
        self.assertTrue(is_tile_walkable(player_eid, 140, 380),
                        "sprite passável no catálogo não deveria travar o tile")

    async def test_dois_jogadores_sem_grupo_looteiam_o_mesmo_harvestable(self):
        """Diferente de corpse de mob: harvestable é público — sem dono, sem
        checagem de grupo. Dois jogadores NÃO relacionados podem sacar."""
        from shared.messages import encode
        session_a, fw_a = await fake_login(self.mgr, "s1", "user_hv_a", 130, 374)
        session_b, fw_b = await fake_login(self.mgr, "s2", "user_hv_b", 200, 400)
        hid = self._make_harvestable(130, 374)

        fw_a.sent.clear()
        await self.mgr.on_message(session_a, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": hid, "take": "all"}))
        results_a = get_msgs_of_type(fw_a, MsgType.LOOT_RESULT)
        self.assertEqual(len(results_a), 1)
        self.assertEqual(results_a[0]["items"][0]["name"], "Espada de treinamento")

        # Segundo harvestable "irmão" pro jogador B saquear (o primeiro já
        # foi esvaziado pelo A) — confirma que B não precisa de grupo/dono
        # pra interagir com um harvestable público igual.
        hid2 = self._make_harvestable(200, 400)
        fw_b.sent.clear()
        await self.mgr.on_message(session_b, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": hid2, "take": "all"}))
        results_b = get_msgs_of_type(fw_b, MsgType.LOOT_RESULT)
        self.assertEqual(len(results_b), 1)
        self.assertEqual(results_b[0]["items"][0]["name"], "Espada de treinamento")

    def test_no_decay_harvestable_nunca_expira(self):
        """timer finito de propósito (produção usa float("inf") redundante
        com no_decay) — só assim o teste prova que é a FLAG no_decay que
        impede o decay, não o timer infinito escondendo o bug."""
        hid = self._make_harvestable(10, 10)
        self.ws_server._corpses[hid]["timer"] = 1.0
        for _ in range(500):
            self.ws_server._process_loot_drops(dt=10.0)
        self.assertIn(hid, self.ws_server._corpses)

    async def test_sweep_de_tick_descobre_harvestable_ao_entrar_no_aoi(self):
        """Jogador loga LONGE do harvestable (fora do AOI), anda pra perto —
        o sweep estacionário GENÉRICO de _build_update_for_session (que
        agora também indexa _harvestable_eids, não só _mob_eids) deve
        achá-lo no MESMO tick que o movimento do próprio player já dispara
        o dispatch (has_pending por causa de deltas['moved'])."""
        session, fw = await fake_login(self.mgr, "s1", "user_hv_c", 0, 0)
        hid = self._make_harvestable(100, 100)
        hv_eid = self._harvestable_eid_for(hid)

        fw.sent.clear()
        from engine.components import TileMovement as _TM_hv
        tm = self.ws_server.world.get_component(session.entity_id, _TM_hv)
        tm.current_tile_x = 100; tm.current_tile_y = 101
        tm.target_tile_x  = 100; tm.target_tile_y  = 101
        self.ws_server._tick(0.05)  # deltas reais (from_tx/from_ty) + dispatch
        await asyncio.sleep(0)  # deixa a task criada por _on_tick rodar

        aoi_updates = get_msgs_of_type(fw, MsgType.AOI_UPDATE)
        spawned = [s for u in aoi_updates for s in u.get("spawned", [])
                  if s.get("kind") == "harvestable" and s.get("corpse_id") == hid]
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0]["eid"], hv_eid)   # eid REAL agora, não mais -hid
        self.assertEqual(spawned[0]["sprite_id"], "pr_box1")

        loot_avail = get_msgs_of_type(fw, MsgType.LOOT_AVAILABLE)
        matching = [m for m in loot_avail if m["corpse_id"] == hid]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["items"][0]["name"], "Espada de treinamento")

    async def test_login_ja_dentro_do_aoi_descobre_harvestable_sem_precisar_de_atividade(self):
        """Bug corrigido antes de M1 revisão 1: um player que loga já
        dentro do AOI de um harvestable parado não era coberto por
        WORLD_STATE nem pelo sweep de tick (que só roda se has_pending
        achar atividade). Fix: get_mobs_in_aoi agora inclui
        _harvestable_eids (mesma função de sempre, sem duplicar lógica) +
        follow-up LOOT_AVAILABLE em _spawn_and_start (inalterado)."""
        hid = self._make_harvestable(130, 374)
        hv_eid = self._harvestable_eid_for(hid)
        session, fw = await fake_login(self.mgr, "s1", "user_hv_d", 130, 374)

        world_states = get_msgs_of_type(fw, MsgType.WORLD_STATE)
        self.assertEqual(len(world_states), 1)
        # Filtra pelo hid do teste — o mapa real (map_1_entities.json)
        # também tem um harvestable de teste na mesma tile (130,374),
        # então pode haver mais de um "kind"=="harvestable" no AOI.
        spawned = [e for e in world_states[0]["entities"]
                  if e.get("kind") == "harvestable" and e.get("corpse_id") == hid]
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0]["eid"], hv_eid)

        loot_avail = get_msgs_of_type(fw, MsgType.LOOT_AVAILABLE)
        matching = [m for m in loot_avail if m["corpse_id"] == hid]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["items"][0]["name"], "Espada de treinamento")

    async def test_world_state_com_harvestable_chama_spawn_remote_harvestable(self):
        """Client-side: _handle_msg_world_state precisa reconhecer
        kind=="harvestable" e chamar _spawn_remote_harvestable (entidade
        real agora) — NUNCA cair no branch "player"/"enemy"."""
        import client.network_handlers as nh_mod

        class _FakeClient:
            _my_eid = 999
            _spawn_remote_mob_called = False
            _spawn_remote_player_called = False
            _spawn_remote_harvestable_args = None

            def _spawn_remote_mob(self, eid, ent):
                self._spawn_remote_mob_called = True

            def _spawn_remote_player_entity(self, eid, data):
                self._spawn_remote_player_called = True

            def _spawn_remote_harvestable(self, eid, data):
                self._spawn_remote_harvestable_args = (eid, data)

        fc = _FakeClient()
        payload = {"tick": 1, "tx": 0, "ty": 0, "entities": [
            {"eid": 42, "kind": "harvestable", "corpse_id": 7, "tx": 5, "ty": 5,
             "name": "Planta", "sprite_id": "pr_box1"},
        ]}
        nh_mod.NetworkHandlers._handle_msg_world_state(fc, payload)
        self.assertFalse(fc._spawn_remote_player_called)
        self.assertFalse(fc._spawn_remote_mob_called)
        self.assertEqual(fc._spawn_remote_harvestable_args, (42, payload["entities"][0]))


class TestHarvestableRespawnM2(unittest.IsolatedAsyncioTestCase):
    """Fase M2 (25/07/2026) — harvestable de posição fixa com
    respawn_s > 0 reabastece sozinho depois de esvaziar POR COMPLETO
    (itens E moedas), resetando também o loot condicional de quest
    (quest_rolls) — decisões do usuário confirmadas via AskUserQuestion.
    Harvestable sem respawn_s (M1 original) nunca reabastece."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    def _make_harvestable(self, tx: int, ty: int, map_file: str = None,
                          name: str = "Arbusto", sprite: str = "pr_box1",
                          items: list = None, coins: int = 0,
                          respawn_s: float = 0) -> int:
        map_key = map_file or self.ws_server._map_file
        spawn_points = {"harvestables": [{
            "x": tx, "y": ty, "name": name, "sprite": sprite,
            "items": items if items is not None else ["training_sword"],
            "coins": coins, "respawn_s": respawn_s,
        }]}
        before_corpses = set(self.ws_server._corpses.keys())
        before_hv_eids = set(self.ws_server._harvestable_eids)
        self.ws_server._create_harvestables_for_map(spawn_points, map_key)
        from engine.components import MapLocation as _MLhv2
        for _new_eid in set(self.ws_server._harvestable_eids) - before_hv_eids:
            if self.ws_server.world.get_component(_new_eid, _MLhv2) is None:
                self.ws_server.world.add_component(_new_eid, _MLhv2(map_key))
        return (set(self.ws_server._corpses.keys()) - before_corpses).pop()

    def test_respawn_s_zero_nunca_reabastece(self):
        """Regressão do M1 original — sem respawn_s, esvaziar é definitivo."""
        hid = self._make_harvestable(10, 10, coins=5, items=[])
        corpse = self.ws_server._corpses[hid]
        corpse["coins"] = 0  # esvazia manualmente (simula saque completo)
        for _ in range(200):
            self.ws_server._tick_harvestable_respawn(dt=10.0)
        self.assertEqual(corpse["items"], [])
        self.assertEqual(corpse["coins"], 0)

    def test_saque_parcial_nao_conta_como_vazio(self):
        """Gatilho do respawn: só conta quando o pote esvazia POR
        COMPLETO — ainda ter moedas (mesmo sem itens) não inicia o timer."""
        hid = self._make_harvestable(20, 20, coins=5, respawn_s=1.0)
        corpse = self.ws_server._corpses[hid]
        corpse["items"] = []   # itens já saqueados, mas moedas continuam
        self.ws_server._tick_harvestable_respawn(dt=100.0)  # bem mais que respawn_s
        self.assertEqual(corpse["coins"], 5)  # não deveria ter sido "reabastecido"
        self.assertEqual(corpse["empty_timer"], 0.0)

    def test_reabastece_depois_do_respawn_s_com_pote_totalmente_vazio(self):
        hid = self._make_harvestable(30, 30, coins=7, items=["training_sword"],
                                     respawn_s=5.0)
        corpse = self.ws_server._corpses[hid]
        corpse["items"] = []
        corpse["coins"] = 0
        corpse["quest_rolls"] = {999: [{"name": "item fantasma"}]}  # simula sorteio antigo

        self.ws_server._tick_harvestable_respawn(dt=3.0)  # < respawn_s
        self.assertEqual(corpse["coins"], 0)
        self.assertEqual(corpse["items"], [])

        self.ws_server._tick_harvestable_respawn(dt=3.0)  # total 6.0 >= 5.0
        self.assertEqual(corpse["coins"], 7)
        self.assertEqual(len(corpse["items"]), 1)
        self.assertEqual(corpse["items"][0]["name"], "Espada de treinamento")
        self.assertEqual(corpse["quest_rolls"], {}, "respawn deveria resetar quest_rolls também")

    async def test_notifica_sessao_que_ja_conhece_ao_reabastecer(self):
        """Sessão que já descobriu o harvestable recebe LOOT_AVAILABLE
        de novo quando ele reabastece — sem precisar redescobrir."""
        # Harvestable ANTES do login — assim WORLD_STATE já descobre a
        # entidade no login (known_eids já inclui), garantindo que o
        # único LOOT_AVAILABLE do refill vem do bloco novo (M2), não de
        # uma descoberta "de primeira vez" competindo no mesmo tick.
        hid = self._make_harvestable(130, 374, coins=9, items=[], respawn_s=2.0)
        session, fw = await fake_login(self.mgr, "s1", "user_hv_resp", 130, 374)
        corpse = self.ws_server._corpses[hid]
        corpse["items"] = []
        corpse["coins"] = 0

        fw.sent.clear()
        self.ws_server._tick_harvestable_respawn(dt=5.0)  # passa do respawn_s
        self.mgr._on_tick(self.ws_server.tick_count, {})
        await asyncio.sleep(0)

        loot_avail = get_msgs_of_type(fw, MsgType.LOOT_AVAILABLE)
        matching = [m for m in loot_avail if m["corpse_id"] == hid]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["coins"], 9)


class TestHarvestableQuestGateM3(unittest.IsolatedAsyncioTestCase):
    """Fase M3 (25/07/2026) — harvestable com `requires_quest` fica
    TOTALMENTE invisível (nem no AOI) pra quem não tem a quest ativa,
    tanto no login (WORLD_STATE) quanto no sweep de tick — decisão
    confirmada com o usuário via AskUserQuestion."""

    QID = "qgate_teste"

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
        QUESTS[self.QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="talk_to_npc", target="X", count=1),),
            reward=QuestReward(xp=1),
        )

    async def asyncTearDown(self):
        from content.quests_data import QUESTS
        QUESTS.pop(self.QID, None)

    def _make_harvestable(self, tx: int, ty: int, map_file: str = None,
                          requires_quest: str = "") -> int:
        map_key = map_file or self.ws_server._map_file
        spawn_points = {"harvestables": [{
            "x": tx, "y": ty, "name": "Item com Trava", "sprite": "pr_box1",
            "items": ["training_sword"], "coins": 0,
            "requires_quest": requires_quest,
        }]}
        before_corpses = set(self.ws_server._corpses.keys())
        before_hv_eids = set(self.ws_server._harvestable_eids)
        self.ws_server._create_harvestables_for_map(spawn_points, map_key)
        from engine.components import MapLocation as _MLhv3
        for _new_eid in set(self.ws_server._harvestable_eids) - before_hv_eids:
            if self.ws_server.world.get_component(_new_eid, _MLhv3) is None:
                self.ws_server.world.add_component(_new_eid, _MLhv3(map_key))
        return (set(self.ws_server._corpses.keys()) - before_corpses).pop()

    async def test_login_sem_quest_nao_ve_harvestable_com_trava(self):
        hid = self._make_harvestable(130, 374, requires_quest=self.QID)
        session, fw = await fake_login(self.mgr, "s1", "user_qg_a", 130, 374)

        world_states = get_msgs_of_type(fw, MsgType.WORLD_STATE)
        spawned = [e for e in world_states[0]["entities"]
                  if e.get("kind") == "harvestable" and e.get("corpse_id") == hid]
        self.assertEqual(len(spawned), 0)
        loot_avail = get_msgs_of_type(fw, MsgType.LOOT_AVAILABLE)
        self.assertFalse(any(m["corpse_id"] == hid for m in loot_avail))

    async def test_login_com_quest_ativa_ve_harvestable_com_trava(self):
        from engine.components import QuestLog
        hid = self._make_harvestable(130, 374, requires_quest=self.QID)
        session, fw = await fake_login(self.mgr, "s1", "user_qg_b", 200, 400)
        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]

        # Anda até perto (dispara o sweep de tick, não o login).
        from engine.components import TileMovement as _TMqg
        tm = self.ws_server.world.get_component(session.entity_id, _TMqg)
        tm.current_tile_x = 130; tm.current_tile_y = 375
        tm.target_tile_x  = 130; tm.target_tile_y  = 375
        fw.sent.clear()
        self.ws_server._tick(0.05)
        await asyncio.sleep(0)

        aoi_updates = get_msgs_of_type(fw, MsgType.AOI_UPDATE)
        spawned = [s for u in aoi_updates for s in u.get("spawned", [])
                  if s.get("kind") == "harvestable" and s.get("corpse_id") == hid]
        self.assertEqual(len(spawned), 1)

    async def test_sweep_de_tick_nao_revela_harvestable_sem_quest(self):
        hid = self._make_harvestable(100, 100, requires_quest=self.QID)
        session, fw = await fake_login(self.mgr, "s1", "user_qg_c", 0, 0)

        from engine.components import TileMovement as _TMqg2
        tm = self.ws_server.world.get_component(session.entity_id, _TMqg2)
        tm.current_tile_x = 100; tm.current_tile_y = 101
        tm.target_tile_x  = 100; tm.target_tile_y  = 101
        fw.sent.clear()
        self.ws_server._tick(0.05)
        await asyncio.sleep(0)

        aoi_updates = get_msgs_of_type(fw, MsgType.AOI_UPDATE)
        spawned = [s for u in aoi_updates for s in u.get("spawned", [])
                  if s.get("kind") == "harvestable" and s.get("corpse_id") == hid]
        self.assertEqual(len(spawned), 0)

    async def test_aceitar_quest_depois_de_logar_revela_no_tick_seguinte(self):
        """Sem relogar: o sweep descobre sozinho assim que a quest fica ativa."""
        from engine.components import QuestLog, TileMovement as _TMqg3
        hid = self._make_harvestable(100, 100, requires_quest=self.QID)
        session, fw = await fake_login(self.mgr, "s1", "user_qg_d", 0, 0)
        tm = self.ws_server.world.get_component(session.entity_id, _TMqg3)
        tm.current_tile_x = 100; tm.current_tile_y = 101
        tm.target_tile_x  = 100; tm.target_tile_y  = 101

        fw.sent.clear()
        self.ws_server._tick(0.05)  # ainda sem a quest — não descobre
        await asyncio.sleep(0)
        aoi_updates = get_msgs_of_type(fw, MsgType.AOI_UPDATE)
        spawned = [s for u in aoi_updates for s in u.get("spawned", [])
                  if s.get("kind") == "harvestable" and s.get("corpse_id") == hid]
        self.assertEqual(len(spawned), 0)

        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]
        # precisa de "atividade" pra disparar o dispatch de novo — um
        # segundo passo de movimento serve.
        tm.current_tile_x = 100; tm.current_tile_y = 102
        tm.target_tile_x  = 100; tm.target_tile_y  = 102
        fw.sent.clear()
        self.ws_server._tick(0.05)
        await asyncio.sleep(0)
        aoi_updates2 = get_msgs_of_type(fw, MsgType.AOI_UPDATE)
        spawned2 = [s for u in aoi_updates2 for s in u.get("spawned", [])
                   if s.get("kind") == "harvestable" and s.get("corpse_id") == hid]
        self.assertEqual(len(spawned2), 1)

    async def test_completar_quest_esconde_harvestable_de_novo(self):
        """Bug real relatado pelo usuário 25/07/2026: completar/entregar a
        quest não escondia o harvestable de novo — a trava só cobria
        "revelar" (sweep pula tudo que já está em known_eids), nunca
        "esconder de novo" depois de já conhecido."""
        from engine.components import QuestLog, TileMovement as _TMqg5
        hid = self._make_harvestable(100, 100, requires_quest=self.QID)
        session, fw = await fake_login(self.mgr, "s1", "user_qg_e", 0, 0)
        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]
        tm = self.ws_server.world.get_component(session.entity_id, _TMqg5)
        tm.current_tile_x = 100; tm.current_tile_y = 101
        tm.target_tile_x  = 100; tm.target_tile_y  = 101

        fw.sent.clear()
        self.ws_server._tick(0.05)   # com a quest ativa — descobre
        await asyncio.sleep(0)
        aoi_updates = get_msgs_of_type(fw, MsgType.AOI_UPDATE)
        spawned = [s for u in aoi_updates for s in u.get("spawned", [])
                  if s.get("kind") == "harvestable" and s.get("corpse_id") == hid]
        self.assertEqual(len(spawned), 1)
        hv_eid = spawned[0]["eid"]

        del ql.active[self.QID]   # completa/entrega a quest
        # mesma "atividade" que os outros testes desta classe usam pra
        # forçar o dispatch de novo — um segundo passo de movimento.
        tm.current_tile_x = 100; tm.current_tile_y = 102
        tm.target_tile_x  = 100; tm.target_tile_y  = 102
        fw.sent.clear()
        self.ws_server._tick(0.05)
        await asyncio.sleep(0)
        aoi_updates2 = get_msgs_of_type(fw, MsgType.AOI_UPDATE)
        despawned2 = {eid for u in aoi_updates2 for eid in u.get("despawned", [])}
        self.assertIn(hv_eid, despawned2,
                      "harvestable deveria ser escondido de novo ao completar a quest")

    def test_harvestable_sem_trava_visivel_pra_qualquer_viewer(self):
        """Regressão: harvestable sem requires_quest continua visível
        (viewer_eid=-1 ou qualquer player) — comportamento M1/M2 intacto."""
        hid = self._make_harvestable(50, 50, requires_quest="")
        from engine.components import Harvestable
        hv_eid = next(eid for eid in self.ws_server._harvestable_eids
                     if self.ws_server.world.get_component(eid, Harvestable).corpse_id == hid)
        self.assertTrue(self.ws_server._harvestable_visible_to(hv_eid, -1))
        self.assertTrue(self.ws_server._harvestable_visible_to(hv_eid, 12345))


class TestItemGrantsQuestM4(unittest.IsolatedAsyncioTestCase):
    """Fase M4 (25/07/2026, REVISADA no mesmo dia — usuário pediu fluxo de
    decisão em vez de automático): item mapeado em ITEM_GRANTS_QUEST NÃO
    inicia a quest sozinho ao ser saqueado — fica só na bag. Quem inicia é
    o QUEST_ACCEPT (o MESMO que o diálogo de NPC já manda), disparado pelo
    popup de aceitar/recusar no clique direito do item
    (client/inventory_handlers.py::_try_open_item_quest_prompt).
    ITEM_GRANTS_QUEST virou METADADO client-side (tag do tooltip + gatilho
    do popup) — request_loot não consulta mais."""

    QID = "qgrant_teste"
    ITEM_NAME = "Pergaminho de Teste"

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef, ITEM_GRANTS_QUEST
        QUESTS[self.QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="collect_item", target="*",
                                     loot_item=self.ITEM_NAME, count=1),),
            reward=QuestReward(xp=1),
        )
        ITEM_GRANTS_QUEST[self.ITEM_NAME] = self.QID

    async def asyncTearDown(self):
        from content.quests_data import QUESTS, ITEM_GRANTS_QUEST
        QUESTS.pop(self.QID, None)
        ITEM_GRANTS_QUEST.pop(self.ITEM_NAME, None)

    def _make_corpse(self, owner_eid: int, extra_item_name: str | None) -> int:
        cid = self.ws_server._next_corpse_id
        self.ws_server._next_corpse_id += 1
        items = [{"name": "Item Comum", "icon_key": "", "item_type": "material",
                  "rarity": "common", "value": 1, "slot": "", "stack": 1}]
        if extra_item_name:
            items.append({"name": extra_item_name, "icon_key": "", "item_type": "material",
                          "rarity": "common", "value": 1, "slot": "", "stack": 1})
        self.ws_server._corpses[cid] = {
            "tx": 130, "ty": 374, "owner_eid": owner_eid,
            "items": items, "coins": 0, "timer": 120.0,
            "map": self.ws_server._map_file,
            "mob_name": "", "mob_race": "", "quest_rolls": {},
        }
        return cid

    async def test_saquear_item_mapeado_nao_inicia_a_quest_sozinho(self):
        from engine.components import QuestLog
        session, _ = await fake_login(self.mgr, "s1", "user_ig_a", 130, 374)
        cid = self._make_corpse(owner_eid=session.entity_id, extra_item_name=self.ITEM_NAME)

        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        result = self.ws_server.request_loot("s1", cid, take="all")
        self.assertTrue(any(it["name"] == self.ITEM_NAME for it in result["items"]))
        self.assertNotIn(self.QID, ql.active)
        self.assertNotIn(self.QID, ql.completed)

    async def test_quest_accept_apos_lootear_inicia_com_objetivo_ja_completo(self):
        """Simula o fluxo real: loota (item vai pra bag), DEPOIS aceita via
        QUEST_ACCEPT (mesmo caminho do popup client-side). O objetivo
        collect_item já nasce completo — o item já está na bag, não faz
        sentido exigir um pickup NOVO (ver quest_logic.py::try_start)."""
        from engine.components import QuestLog, Inventory, Item
        import engine.quest_logic as quest_logic
        session, _ = await fake_login(self.mgr, "s1", "user_ig_b", 130, 374)
        cid = self._make_corpse(owner_eid=session.entity_id, extra_item_name=self.ITEM_NAME)
        self.ws_server.request_loot("s1", cid, take="all")
        # request_loot NÃO toca a Inventory do servidor (loot online é
        # client-authoritative pro item em si — só o Wallet é servidor puro
        # aqui) — simula o INV_SYNC que o cliente manda logo depois de
        # aplicar o LOOT_RESULT localmente.
        inv = self.ws_server.world.get_component(session.entity_id, Inventory)
        inv.items.append(Item(self.ITEM_NAME, "material", slot=None, max_stack=1))

        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        started = quest_logic.try_start(self.ws_server.world, session.entity_id, ql, self.QID)
        self.assertTrue(started)
        self.assertEqual(ql.active[self.QID], [1])   # já completo — item já na bag

    async def test_item_nao_mapeado_nao_concede_nada(self):
        from engine.components import QuestLog
        session, _ = await fake_login(self.mgr, "s1", "user_ig_c", 130, 374)
        cid = self._make_corpse(owner_eid=session.entity_id, extra_item_name=None)

        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        self.ws_server.request_loot("s1", cid, take="all")
        self.assertNotIn(self.QID, ql.active)

    async def test_funciona_pra_harvestable_tambem_nao_so_mob(self):
        """owner_eid=-1 (harvestable de mapa, público) também deixa o item
        na bag sem iniciar nada — decisão confirmada: vale pra QUALQUER
        origem do item, o comportamento (ou ausência dele aqui) é o mesmo."""
        from engine.components import QuestLog
        session, _ = await fake_login(self.mgr, "s1", "user_ig_d", 130, 374)
        cid = self._make_corpse(owner_eid=-1, extra_item_name=self.ITEM_NAME)

        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        result = self.ws_server.request_loot("s1", cid, take="all")
        self.assertTrue(any(it["name"] == self.ITEM_NAME for it in result["items"]))
        self.assertNotIn(self.QID, ql.active)


class TestHarvestableZone(unittest.IsolatedAsyncioTestCase):
    """Zona de itens (25/07/2026) — decisões confirmadas via
    AskUserQuestion: mistura de sub-tipos na MESMA zona (mesmo padrão de
    spawn_zones de mob); nó esgotado SOME de verdade (ao contrário do
    harvestable de posição fixa, que fica visível vazio até reabastecer no
    lugar); nó novo nasce em posição ALEATÓRIA dentro do raio (não sempre
    no mesmo lugar); 1 cooldown só, pra zona inteira (não por sub-tipo).
    (140, 380) confirmado majoritariamente caminhável num raio de 3 no
    mapa real (3 de 49 tiles sólidos — checado à parte antes de escrever
    o teste, mesmo cuidado do teste de colisão da Fase M1)."""

    CENTER = (140, 380)
    RADIUS = 3

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        # Isola de qualquer harvestable_zone real já presente no mapa
        # carregado (ex.: conteúdo que o usuário for adicionando em
        # map_1_entities.json) — sem isso, testes que checam contagem/
        # chamada exata (ex. mocked.assert_called_once()) ficam frágeis a
        # mudança de conteúdo do mapa real, que não tem nada a ver com a
        # lógica sendo testada aqui.
        self.ws_server._harvestable_zones.clear()
        self.ws_server._harvestable_zone_active.clear()
        self.ws_server._harvestable_zone_timers.clear()

    def _make_zone(self, count_a: int = 3, count_b: int = 2,
                   respawn_cooldown: float = 1.0, requires_quest: str = "") -> int:
        zones_data = [{
            "x": self.CENTER[0], "y": self.CENTER[1], "radius": self.RADIUS,
            "respawn_cooldown": respawn_cooldown, "requires_quest": requires_quest,
            "spawns": [
                {"name": "Cogumelo", "sprite": "pl_bush3",
                 "items": ["training_sword"], "coins": 0, "count": count_a},
                {"name": "Arbusto", "sprite": "pl_bush1",
                 "items": [], "coins": 5, "count": count_b},
            ],
        }]
        before = set(self.ws_server._harvestable_zones.keys())
        self.ws_server._create_harvestable_zones_for_map(zones_data, self.ws_server._map_file)
        return (set(self.ws_server._harvestable_zones.keys()) - before).pop()

    async def test_zona_enfileira_timers_iniciais_ao_registrar(self):
        """Registrar a zona (equivalente ao carregamento do mapa) já
        enfileira 1 timer por slot (3+2=5), mas ainda não spawna nada —
        nascimento em si só acontece no primeiro tick."""
        zid = self._make_zone(count_a=3, count_b=2)
        zone = self.ws_server._harvestable_zones[zid]
        self.assertEqual((zone["x"], zone["y"]), self.CENTER)
        self.assertEqual(len(zone["spawns"]), 2)
        self.assertEqual(len(self.ws_server._harvestable_zone_timers[zid]), 5)
        self.assertEqual(self.ws_server._harvestable_zone_active[zid], {})

    async def test_tick_spawna_todos_os_nos_ate_o_count_total_misturando_subtipos(self):
        zid = self._make_zone(count_a=3, count_b=2)
        self.ws_server._tick_harvestable_zones(1.0)  # dt grande zera todos os timers iniciais

        active = self.ws_server._harvestable_zone_active[zid]
        self.assertEqual(len(active), 5)
        subtype_counts: dict = {}
        for hid, idx in active.items():
            subtype_counts[idx] = subtype_counts.get(idx, 0) + 1
            self.assertIn(hid, self.ws_server._corpses)
            eid = self.ws_server._harvestable_hid_to_eid.get(hid)
            self.assertIsNotNone(eid)
            self.assertIn(eid, self.ws_server._harvestable_eids)
        self.assertEqual(subtype_counts, {0: 3, 1: 2})
        names = {self.ws_server._corpses[hid]["name"] for hid in active}
        self.assertEqual(names, {"Cogumelo", "Arbusto"})

    async def test_esgotar_no_remove_entidade_e_dispara_despawn_generico(self):
        """Diferente do harvestable de posição fixa: nó de zona esgotado é
        REMOVIDO de verdade (world.remove_entity + ENTITY_DESPAWN genérico
        via _despawned_this_tick), não fica visível vazio."""
        zid = self._make_zone(count_a=1, count_b=0)
        self.ws_server._tick_harvestable_zones(1.0)
        hid = next(iter(self.ws_server._harvestable_zone_active[zid]))
        eid = self.ws_server._harvestable_hid_to_eid[hid]

        self.ws_server._corpses[hid]["items"] = []
        self.ws_server._corpses[hid]["coins"] = 0
        self.ws_server._despawned_this_tick.clear()
        self.ws_server._tick_harvestable_zones(0.1)

        self.assertNotIn(hid, self.ws_server._harvestable_zone_active[zid])
        self.assertNotIn(hid, self.ws_server._corpses)
        self.assertNotIn(hid, self.ws_server._harvestable_hid_to_eid)
        self.assertNotIn(eid, self.ws_server._harvestable_eids)
        self.assertTrue(any(d["eid"] == eid for d in self.ws_server._despawned_this_tick))

    async def test_no_reaparece_apos_cooldown_em_posicao_nova_nao_fixa_na_antiga(self):
        """Decisão confirmada (NÃO é o padrão recomendado por mim — o
        usuário escolheu de propósito o oposto): reposicionamento em zona
        sorteia uma posição NOVA dentro do raio, nunca reusa a posição do
        nó esgotado. Mocka _pick_harvestable_zone_tile pra provar que o
        respawn chama uma amostragem NOVA (em vez de reaproveitar
        tx/ty do corpse removido, que seria o bug 'errado por acidente')."""
        from unittest.mock import patch
        zid = self._make_zone(count_a=1, count_b=0, respawn_cooldown=1.0)
        self.ws_server._tick_harvestable_zones(1.0)
        hid_old = next(iter(self.ws_server._harvestable_zone_active[zid]))
        old_tile = (self.ws_server._corpses[hid_old]["tx"], self.ws_server._corpses[hid_old]["ty"])

        self.ws_server._corpses[hid_old]["items"] = []
        self.ws_server._corpses[hid_old]["coins"] = 0
        self.ws_server._tick_harvestable_zones(0.1)  # detecta esgotado, enfileira timer
        self.assertEqual(len(self.ws_server._harvestable_zone_active[zid]), 0)

        forced_tile = (old_tile[0] + 1, old_tile[1] + 1)
        with patch.object(self.ws_server, "_pick_harvestable_zone_tile",
                          return_value=forced_tile) as mocked:
            self.ws_server._tick_harvestable_zones(2.0)  # passa do cooldown
        mocked.assert_called_once()

        self.assertEqual(len(self.ws_server._harvestable_zone_active[zid]), 1)
        hid_new = next(iter(self.ws_server._harvestable_zone_active[zid]))
        self.assertNotEqual(hid_new, hid_old)
        new_tile = (self.ws_server._corpses[hid_new]["tx"], self.ws_server._corpses[hid_new]["ty"])
        self.assertEqual(new_tile, forced_tile)
        self.assertNotEqual(new_tile, old_tile)

    async def test_zona_com_requires_quest_aplica_pra_qualquer_no_nascido_nela(self):
        """requires_quest é 1 valor SÓ pra zona inteira — todo nó nascido
        nela (de qualquer sub-tipo) herda a mesma trava (Fase M3 reaproveitada
        sem mudança nenhuma no mecanismo de visibilidade)."""
        from engine.components import Harvestable
        zid = self._make_zone(count_a=1, count_b=1, requires_quest="qz_teste")
        self.ws_server._tick_harvestable_zones(1.0)
        for hid in self.ws_server._harvestable_zone_active[zid]:
            eid = self.ws_server._harvestable_hid_to_eid[hid]
            hv = self.ws_server.world.get_component(eid, Harvestable)
            self.assertEqual(hv.requires_quest, "qz_teste")
            # viewer_eid=-1 (sem contexto de sessão) é tratado como "sem
            # trava" por convenção (mesmo princípio já validado no M3) —
            # só um viewer_eid real SEM a quest é bloqueado de verdade.
            self.assertTrue(self.ws_server._harvestable_visible_to(eid, -1))
            self.assertFalse(self.ws_server._harvestable_visible_to(eid, 99999))


class TestArenaDispatchSemMovimento(unittest.IsolatedAsyncioTestCase):
    """Bug real relatado pelo usuário 21/07/2026: "só chamou a arena
    quando movi o personagem" + "cliquei em aceitar e ninguém entrou".
    Causa raiz: `_on_tick`'s `has_pending` (server/session.py) decide se
    vale a pena rodar `_dispatch_tick_deltas` olhando só `deltas` (que
    nunca carrega nada de arena) + uma lista fixa de outros buffers —
    os 4 buffers de evento de arena (`_arena_match_found/start/end/
    result_events_this_tick`) nunca entravam nessa lista. Sem NENHUMA
    outra atividade no tick (ninguém se movendo/atacando/etc.), o
    early-return descartava o dispatch inteiro e o evento ficava PRESO
    no buffer até alguém se mexer por qualquer outro motivo — o que
    também explicava o aceite "não fazer nada" (ARENA_MATCH_START, que
    carrega a confirmação do teleporte já feito no servidor, ficava
    preso do mesmo jeito)."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    async def _make_duo(self, prefix: str, tile: tuple):
        sa, fwa = await fake_login(self.mgr, f"{prefix}a", f"{prefix}usera", *tile)
        sb, fwb = await fake_login(self.mgr, f"{prefix}b", f"{prefix}userb", *tile)
        self.assertIsNone(self.ws_server.request_party_invite(sa.entity_id, sb.entity_id))
        self.ws_server.respond_party_invite(sb.entity_id, accept=True)
        return (sa, fwa), (sb, fwb)

    _UNRELATED_BUFFERS = (
        "_skill_results_this_tick", "_skill_effects_this_tick",
        "_pending_loot_notifications", "_pending_stats_updates",
        "_expired_corpses_this_tick", "_player_hp_broadcasts_this_tick",
        "_skill_levels_broadcasts_this_tick", "_quest_update_broadcasts_this_tick",
        "_pending_sound_events",
    )

    def _clear_unrelated_buffers(self):
        """Zera tudo que `has_pending` (server/session.py::_on_tick) também
        olha além de `deltas` — o mundo de teste roda em cima do banco real
        (data/game.db, ver make_world_server/WorldServer()), então mobs/
        regen/efeitos residuais de outras sessões de teste manual podem
        deixar esses buffers não-vazios por motivo nenhum ligado à arena.
        Zerar explicitamente garante que só a arena pode satisfazer
        has_pending nestes testes — sem isso, o teste passa mesmo sem o fix
        (falso positivo já observado e diagnosticado nesta mesma investigação)."""
        for attr in self._UNRELATED_BUFFERS:
            getattr(self.ws_server, attr).clear()

    async def test_arena_match_found_chega_sem_ninguem_se_mover(self):
        # Prefixos puramente alfabéticos e distintos entre si — _valid_char_name
        # (tests/test_session.py) descarta dígitos ao derivar o nome do
        # personagem a partir do username, então algo tipo "fg1"/"fg2" colide
        # no MESMO nome "fg" depois do strip (bug do teste, não do servidor).
        (a1, fw_a1), (a2, fw_a2) = await self._make_duo("krondor", (10, 10))
        (b1, fw_b1), (b2, fw_b2) = await self._make_duo("zephyra", (12, 10))

        self.assertIsNone(self.ws_server.request_arena_queue_join(a1.entity_id))
        self.assertIsNone(self.ws_server.request_arena_queue_join(b1.entity_id))
        for fw in (fw_a1, fw_a2, fw_b1, fw_b2):
            fw.sent.clear()

        # Chama só o pareamento de arena diretamente — NUNCA passa por
        # `_tick()` completo. Rodar o tick inteiro aqui roda TAMBÉM
        # SpawnZoneSystem/EnemyAISystem/regen/etc contra o mundo real
        # (banco compartilhado com testes manuais), que enche `deltas`
        # por acaso e mascara o bug (já provado via diagnóstico: `deltas`
        # tinha "spawned"/"effects" mesmo sem NENHUMA atividade de arena,
        # fazendo o teste passar igual com ou sem o fix). Chamando só
        # `_tick_arena_queue()` + `_on_tick(tick, {})` isolamos exatamente
        # o que o bug reportado testava: "nenhuma outra atividade no
        # tick, só a arena tem algo pra dispachar".
        self.ws_server._tick_arena_queue()
        self._clear_unrelated_buffers()
        self.mgr._on_tick(self.ws_server.tick_count, {})
        await asyncio.sleep(0)   # processa o create_task do dispatch

        for fw in (fw_a1, fw_a2, fw_b1, fw_b2):
            found = get_msgs_of_type(fw, MsgType.ARENA_MATCH_FOUND)
            self.assertEqual(len(found), 1,
                "ARENA_MATCH_FOUND deveria chegar no mesmo tick do pareamento, "
                "sem depender de qualquer outra atividade no tick")

    async def test_arena_match_start_chega_ao_aceitar_sem_ninguem_se_mover(self):
        (a1, fw_a1), (a2, fw_a2) = await self._make_duo("morvane", (10, 10))
        (b1, fw_b1), (b2, fw_b2) = await self._make_duo("thalrix", (12, 10))

        self.assertIsNone(self.ws_server.request_arena_queue_join(a1.entity_id))
        self.assertIsNone(self.ws_server.request_arena_queue_join(b1.entity_id))
        self.ws_server._tick_arena_queue()
        self._clear_unrelated_buffers()
        self.mgr._on_tick(self.ws_server.tick_count, {})
        await asyncio.sleep(0)
        fw_a1.sent.clear()

        reason = self.ws_server.request_arena_accept(a1.entity_id)
        self.assertIsNone(reason)
        self._clear_unrelated_buffers()
        self.mgr._on_tick(self.ws_server.tick_count, {})
        await asyncio.sleep(0)

        self.assertEqual(len(get_msgs_of_type(fw_a1, MsgType.ARENA_MATCH_START)), 1,
            "ARENA_MATCH_START deveria chegar assim que aceita, sem depender de "
            "qualquer outra atividade no tick")
        self.assertEqual(len(get_msgs_of_type(fw_a1, MsgType.ZONE_CHANGE)), 1,
            "ZONE_CHANGE (teleporte pra arena) deveria chegar junto, sem depender "
            "de qualquer outra atividade no tick")


class TestPartyDispatchSemMovimento(unittest.IsolatedAsyncioTestCase):
    """Bug real relatado pelo usuário 22/07/2026: HUD de grupo (slots no
    canto superior esquerdo) só atualizava quando algum player se movia
    depois de aceitar um convite. Mesma classe de bug já corrigida pra
    arena (§34.34.1/§34.34.2, ARQUITETURA_ONLINE.md) — `has_pending`
    (server/session.py::_on_tick) não olhava `_party_state_events_this_tick`
    (nem `_duel_end_events_this_tick`/`_trade_cancellations_this_tick`/
    `_skill_position_corrections`, mesmo problema, mesmo fix)."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    async def test_party_state_chega_ao_aceitar_convite_sem_ninguem_se_mover(self):
        sa, fw_a = await fake_login(self.mgr, "pdsm_a", "pdsmusera", 10, 10)
        sb, fw_b = await fake_login(self.mgr, "pdsm_b", "pdsmuserb", 12, 10)

        self.assertIsNone(self.ws_server.request_party_invite(sa.entity_id, sb.entity_id))
        for fw in (fw_a, fw_b):
            fw.sent.clear()

        # Só o aceite, direto — NUNCA o _tick() inteiro (mesma razão do
        # helper de arena: rodar o mundo todo pode mascarar o bug com
        # atividade alheia de spawn zone/regen).
        self.ws_server.respond_party_invite(sb.entity_id, accept=True)
        for attr in ("_skill_results_this_tick", "_skill_effects_this_tick",
                     "_pending_loot_notifications", "_pending_stats_updates",
                     "_expired_corpses_this_tick", "_player_hp_broadcasts_this_tick",
                     "_skill_levels_broadcasts_this_tick", "_quest_update_broadcasts_this_tick",
                     "_pending_sound_events"):
            getattr(self.ws_server, attr).clear()
        self.mgr._on_tick(self.ws_server.tick_count, {})
        await asyncio.sleep(0)

        for fw in (fw_a, fw_b):
            found = get_msgs_of_type(fw, MsgType.PARTY_STATE)
            self.assertEqual(len(found), 1,
                "PARTY_STATE deveria chegar no mesmo tick do aceite, sem "
                "depender de qualquer outra atividade no tick")


class TestBattlegroundDispatchSemMovimento(unittest.IsolatedAsyncioTestCase):
    """Nexus derrubado (02/08/2026, pedido do usuário) — MESMA classe de
    bug já corrigida pra arena/party: `has_pending` (server/session.py::
    _on_tick) precisa olhar `_dbg_bg_tick._state["pending_match_result"]`
    e `["pending_forced_leave_notify"]` (server/debug_battleground.py),
    senão o placar/retorno forçado ficam presos no buffer até atividade
    alheia (movimento de outro player, etc.) destravar o dispatch."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    def _clear_unrelated_buffers(self):
        for attr in ("_skill_results_this_tick", "_skill_effects_this_tick",
                     "_pending_loot_notifications", "_pending_stats_updates",
                     "_expired_corpses_this_tick", "_player_hp_broadcasts_this_tick",
                     "_skill_levels_broadcasts_this_tick", "_quest_update_broadcasts_this_tick",
                     "_pending_sound_events", "_arena_match_found_events_this_tick",
                     "_arena_match_start_events_this_tick", "_arena_match_end_events_this_tick",
                     "_arena_match_result_events_this_tick", "_arena_gate_open_events_this_tick",
                     "_party_state_events_this_tick", "_duel_end_events_this_tick",
                     "_trade_cancellations_this_tick", "_skill_position_corrections"):
            getattr(self.ws_server, attr).clear()
        from server import debug_battleground as _dbg
        _dbg._state["pending_gate_open_eids"] = []

    async def test_bg_match_result_chega_sem_ninguem_se_mover(self):
        from engine.components import Faction
        from server import debug_battleground as bg
        sa, fw_a = await fake_login(self.mgr, "bgdsm_a", "bgdsmusera", 10, 10)
        bg._state["members"] = {sa.entity_id}
        bg._state["stat_snapshots"] = {sa.entity_id: {"kills": 0, "deaths": 0, "farm": 0, "damage": 0}}
        bg._state["match_decided"] = False
        bg._state["result_deadline"] = None
        bg._state["pending_match_result"] = []
        self.ws_server.world.add_component(sa.entity_id, Faction("arena_time_a"))
        fw_a.sent.clear()
        try:
            bg.notify_nexus_destroyed(self.ws_server, bg.DEBUG_BG_INSTANCE_KEY,
                                      "arena_time_b", sa.entity_id)
            self._clear_unrelated_buffers()
            self.mgr._on_tick(self.ws_server.tick_count, {})
            await asyncio.sleep(0)

            found = get_msgs_of_type(fw_a, MsgType.BG_MATCH_RESULT)
            self.assertEqual(len(found), 1,
                "BG_MATCH_RESULT deveria chegar no mesmo tick do nexus derrubado, "
                "sem depender de qualquer outra atividade no tick")
        finally:
            bg._state["members"] = set()
            bg._state["stat_snapshots"] = {}
            bg._state["match_decided"] = False
            bg._state["result_deadline"] = None
            bg._state["pending_match_result"] = []

    async def test_forced_leave_zone_change_chega_sem_ninguem_se_mover(self):
        import time
        from engine.components import Faction
        from server import debug_battleground as bg
        sa, fw_a = await fake_login(self.mgr, "bgdsm_b", "bgdsmuserb", 10, 10)
        bg._state["members"] = {sa.entity_id}
        bg._state["return_pos"] = {sa.entity_id: (self.ws_server.MAP_FILE, 50, 50)}
        bg._state["stat_snapshots"] = {sa.entity_id: {"kills": 0, "deaths": 0, "farm": 0, "damage": 0}}
        bg._state["match_decided"] = True
        bg._state["result_deadline"] = time.time() - 1
        bg._state["pending_forced_leave_notify"] = []
        self.ws_server.world.add_component(sa.entity_id, Faction("arena_time_a"))
        fw_a.sent.clear()
        try:
            bg._tick_bg_results_timeout(self.ws_server)
            self._clear_unrelated_buffers()
            self.mgr._on_tick(self.ws_server.tick_count, {})
            await asyncio.sleep(0)

            found = get_msgs_of_type(fw_a, MsgType.ZONE_CHANGE)
            self.assertEqual(len(found), 1,
                "ZONE_CHANGE do timeout forçado deveria chegar no mesmo tick, "
                "sem depender de qualquer outra atividade no tick")
        finally:
            bg._state["members"] = set()
            bg._state["return_pos"] = {}
            bg._state["stat_snapshots"] = {}
            bg._state["match_decided"] = False
            bg._state["result_deadline"] = None
            bg._state["pending_forced_leave_notify"] = []


class TestUnstuck(unittest.IsolatedAsyncioTestCase):
    """Botão "Voltar ao Spawn" (bug real relatado pelo usuário 22/07/2026):
    só mudava tile_x/tile_y pro spawn de map_1, nunca o mapa em si — quem
    estava numa caverna ficava preso lá com a coordenada errada."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    async def test_unstuck_fora_do_mapa_principal_troca_de_mapa(self):
        session, fw = await fake_login(self.mgr, "unstuck_a", "unstuckusera")
        eid = session.entity_id
        self.ws_server.transfer_player(session.session_id, eid,
                                        "maps/map_cave_east.csv", 10, 10)
        self.assertEqual(self.ws_server.get_player_map(session.session_id),
                         "maps/map_cave_east.csv")
        fw.sent.clear()

        await self.mgr._handle_unstuck(session, {}, 0)

        self.assertEqual(self.ws_server.get_player_map(session.session_id),
                         self.ws_server._map_file)
        zone_changes = get_msgs_of_type(fw, MsgType.ZONE_CHANGE)
        self.assertEqual(len(zone_changes), 1,
            "deveria mandar ZONE_CHANGE ao trocar de mapa pro spawn")
        self.assertEqual(zone_changes[0]["map_file"], self.ws_server._map_file)

    async def test_unstuck_ja_no_mapa_principal_nao_manda_zone_change(self):
        session, fw = await fake_login(self.mgr, "unstuck_b", "unstuckuserb")
        self.assertEqual(self.ws_server.get_player_map(session.session_id),
                         self.ws_server._map_file)
        fw.sent.clear()

        await self.mgr._handle_unstuck(session, {}, 0)

        self.assertEqual(len(get_msgs_of_type(fw, MsgType.ZONE_CHANGE)), 0,
            "já estava no mapa principal — não deveria mandar ZONE_CHANGE")
        self.assertEqual(len(get_msgs_of_type(fw, MsgType.ENTITY_MOVE)), 1)


class TestZoneChangeReq(unittest.IsolatedAsyncioTestCase):
    """ZONE_CHANGE_REQ (C→S) — usado por transições normais de mapa (ex.:
    entrar numa caverna) E, desde 23/07/2026, pelo teleporte de mapa do
    menu de debug (F12): antes chamava `_do_transition` client-side direto
    (nunca avisava o servidor — bug real relatado pelo usuário: tela preta
    + minimapa preso no mapa antigo, e "Voltar ao Spawn" via UNSTUCK não
    via troca nenhuma pra desfazer, já que o servidor nunca tinha saído do
    mapa original). Ver game.py (bloco de `_debug_teleport_map`).

    Carrega sob demanda (mesmo dia, pedido do usuário: "queria poder
    teleportar pra arena pra poder editar o mapa") mapas válidos ainda
    não carregados como bundle standalone — templates só instanciados
    por partida (ex.: arena_poco_negro.csv) nunca tinham bundle próprio
    fora de uma partida real, então o teleporte de debug pra lá falhava
    silenciosamente até essa mudança."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    async def test_mapa_valido_troca_de_mapa_e_manda_zone_change(self):
        session, fw = await fake_login(self.mgr, "zcr_a", "zcruser_a")
        fw.sent.clear()

        await self.mgr._handle_zone_change_req(session, {
            "to_map": "maps/map_cave_east.csv", "target_x": 10, "target_y": 10,
        }, 0)

        self.assertEqual(self.ws_server.get_player_map(session.session_id),
                         "maps/map_cave_east.csv")
        zone_changes = get_msgs_of_type(fw, MsgType.ZONE_CHANGE)
        self.assertEqual(len(zone_changes), 1)
        self.assertEqual(zone_changes[0]["map_file"], "maps/map_cave_east.csv")
        self.assertEqual((zone_changes[0]["target_x"], zone_changes[0]["target_y"]), (10, 10))

    async def test_template_de_arena_carrega_sob_demanda_e_teleporta(self):
        """arena_poco_negro.csv nunca é pré-carregado no boot (só via
        _load_instance, por partida) — o primeiro ZONE_CHANGE_REQ pra lá
        precisa carregar um bundle standalone na hora, não falhar."""
        session, fw = await fake_login(self.mgr, "zcr_c", "zcruser_c")
        fw.sent.clear()
        self.assertNotIn("maps/arena_poco_negro.csv", self.ws_server._map_bundles)

        await self.mgr._handle_zone_change_req(session, {
            "to_map": "maps/arena_poco_negro.csv", "target_x": 13, "target_y": 13,
        }, 0)

        self.assertIn("maps/arena_poco_negro.csv", self.ws_server._map_bundles)
        self.assertEqual(self.ws_server.get_player_map(session.session_id),
                         "maps/arena_poco_negro.csv")
        zone_changes = get_msgs_of_type(fw, MsgType.ZONE_CHANGE)
        self.assertEqual(len(zone_changes), 1)
        self.assertEqual(zone_changes[0]["map_file"], "maps/arena_poco_negro.csv")

    async def test_caminho_fora_de_maps_e_recusado(self):
        """Guard de segurança: to_map fora de maps/ ou com ".." nunca deve
        tentar carregar nada do disco (cliente malicioso forjando o
        payload de ZONE_CHANGE_REQ)."""
        session, fw = await fake_login(self.mgr, "zcr_d", "zcruser_d")
        original_map = self.ws_server.get_player_map(session.session_id)
        fw.sent.clear()

        for bad_path in ("../secret.csv", "server/world_server.py", "maps/../server/world_server.py"):
            await self.mgr._handle_zone_change_req(session, {
                "to_map": bad_path, "target_x": 0, "target_y": 0,
            }, 0)
            self.assertEqual(self.ws_server.get_player_map(session.session_id), original_map,
                             f"não deveria ter teleportado com to_map={bad_path!r}")
        self.assertEqual(len(get_msgs_of_type(fw, MsgType.ZONE_CHANGE)), 0)

    async def test_mapa_inexistente_e_ignorado_sem_travar(self):
        session, fw = await fake_login(self.mgr, "zcr_e", "zcruser_e")
        original_map = self.ws_server.get_player_map(session.session_id)
        fw.sent.clear()

        await self.mgr._handle_zone_change_req(session, {
            "to_map": "maps/nao_existe_de_verdade.csv", "target_x": 0, "target_y": 0,
        }, 0)

        self.assertEqual(self.ws_server.get_player_map(session.session_id), original_map)
        self.assertEqual(len(get_msgs_of_type(fw, MsgType.ZONE_CHANGE)), 0)


# ─────────────────────────────────────────────────────────────────────────────
# Visão compartilhada de time (30/07/2026, pedido do usuário)
# ─────────────────────────────────────────────────────────────────────────────

class TestAllyVisionSharing(unittest.IsolatedAsyncioTestCase):
    """SÓ conteúdo instanciado (arena hoje, battlefield/dungeon no futuro)
    — gate: Faction EXPLÍCITA no player (mundo aberto normal nunca tem).
    Reaproveita Faction como já existe, zero conceito novo de time.
    Raios por tipo de aliado: player=ALLY_VISION_RADIUS_PLAYER (15, igual
    AOI_RADIUS), torre=ALLY_VISION_RADIUS_TOWER (18), minion/NPC=
    ALLY_VISION_RADIUS_MINION (8) — pedido explícito do usuário."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    async def _run_ticks_async(self, n: int):
        for _ in range(n):
            self.ws_server._tick(0.05)
            await asyncio.sleep(0)

    async def test_mundo_aberto_sem_faction_nao_gera_centro_de_aliado(self):
        """Players sem Faction (mundo aberto normal) nunca geram centro de
        visão de time — custo ~zero fora de contexto de time (regressão:
        visão compartilhada NUNCA deve vazar pro grupo/mundo aberto)."""
        await fake_login(self.mgr, "s1", "avs_open_a", 115, 389)
        await fake_login(self.mgr, "s2", "avs_open_b", 117, 389)
        self.assertEqual(self.mgr._compute_ally_vision_centers(), {})

    async def test_teammate_estende_visao_alem_do_proprio_aoi(self):
        """A está longe de um mob M (fora do próprio AOI), mas o teammate B
        (mesma Faction, mesmo mapa) está perto de M — M deve entrar no
        known_eids de A via a visão de B (raio de player = 15 tiles)."""
        from engine.components import Faction, MapLocation
        from engine.entity_factory import create_enemy

        session_a, fw_a = await fake_login(self.mgr, "s1", "avs_a", 10, 10)
        session_b, fw_b = await fake_login(self.mgr, "s2", "avs_b", 100, 100)
        self.ws_server.world.add_component(session_a.entity_id, Faction(faction_id="time_a"))
        self.ws_server.world.add_component(session_b.entity_id, Faction(faction_id="time_a"))

        map_file = self.ws_server.get_player_map(session_a.session_id)
        mob_eid = create_enemy(self.ws_server.world, 103, 100, race="Lobo")
        self.ws_server.world.add_component(mob_eid, MapLocation(map_file))

        await self._run_ticks_async(3)
        self.assertIn(mob_eid, session_a.known_eids,
                      "mob perto do teammate B deveria aparecer pra A via visão de time")

    async def test_torre_aliada_contribui_seu_proprio_raio_configurado(self):
        """Torre aliada contribui o raio CONFIGURADO em content/tower_
        definitions.py::TOWER_TABLE["torre_de_fogo"]["vision_radius_tiles"]
        (por-tipo, ajustável pelo usuário — §34.72.2) — não um valor
        hardcoded aqui. Mob posicionado a (raio-1) tiles da torre, e A bem
        longe de ambos (só a torre poderia revelar o mob)."""
        from engine.components import Faction, MapLocation
        from engine.entity_factory import create_enemy, create_tower
        from content.tower_definitions import TOWER_TABLE

        radius = TOWER_TABLE["torre_de_fogo"]["vision_radius_tiles"]

        session_a, _ = await fake_login(self.mgr, "s1", "avs_tw_a", 10, 10)
        self.ws_server.world.add_component(session_a.entity_id, Faction(faction_id="time_a"))
        map_file = self.ws_server.get_player_map(session_a.session_id)

        tower_eid = create_tower(self.ws_server.world, 200, 200, "torre_de_fogo",
                                 faction_id="time_a")
        self.ws_server.world.add_component(tower_eid, MapLocation(map_file))

        mob_eid = create_enemy(self.ws_server.world, 200, 200 + radius - 1, race="Lobo")
        self.ws_server.world.add_component(mob_eid, MapLocation(map_file))

        await self._run_ticks_async(3)
        self.assertIn(mob_eid, session_a.known_eids,
                      "mob dentro do raio configurado da torre aliada deveria ter aparecido pra A")

    async def test_raio_de_visao_da_torre_vem_do_dado_por_tipo_nao_de_constante_global(self):
        """Pedido do usuário (30/07/2026): o raio de visão compartilhada
        precisa ser ajustável POR TIPO de torre (content/tower_
        definitions.py::TOWER_TABLE["vision_radius_tiles"]), não uma
        constante global fixa — `_compute_ally_vision_centers` deve ler
        `Tower.vision_radius_tiles` (gravado por create_tower a partir da
        definição), não ALLY_VISION_RADIUS_TOWER direto."""
        from engine.components import Faction, MapLocation, Tower as _TowerComp
        from engine.entity_factory import create_enemy, create_tower

        session_a, _ = await fake_login(self.mgr, "s1", "avs_twr_a", 10, 10)
        self.ws_server.world.add_component(session_a.entity_id, Faction(faction_id="time_a"))
        map_file = self.ws_server.get_player_map(session_a.session_id)

        tower_eid = create_tower(self.ws_server.world, 100, 100, "torre_de_fogo",
                                 faction_id="time_a")
        self.ws_server.world.add_component(tower_eid, MapLocation(map_file))
        # Override pontual (simula uma torre de tipo/config diferente do
        # default 18) — se o código lesse a constante em vez do componente,
        # este override não teria efeito nenhum.
        self.ws_server.world.get_component(tower_eid, _TowerComp).vision_radius_tiles = 5

        mob_eid = create_enemy(self.ws_server.world, 100, 110, race="Lobo")  # 10 tiles da torre
        self.ws_server.world.add_component(mob_eid, MapLocation(map_file))

        await self._run_ticks_async(3)
        self.assertNotIn(mob_eid, session_a.known_eids,
                         "com vision_radius_tiles=5 na torre, mob a 10 tiles não deveria aparecer")

    async def test_minion_aliado_contribui_raio_8_nao_15(self):
        """Minion/NPC aliado (ALLY_VISION_RADIUS_MINION=8) enxerga MENOS
        longe que um player aliado (15) — mob a 10 tiles do minion NÃO
        deveria aparecer (10 > 8, mesmo estando dentro de 15)."""
        from engine.components import Faction, MapLocation
        from engine.entity_factory import create_enemy, create_combat_npc

        session_a, _ = await fake_login(self.mgr, "s1", "avs_mn_a", 10, 10)
        self.ws_server.world.add_component(session_a.entity_id, Faction(faction_id="time_a"))
        map_file = self.ws_server.get_player_map(session_a.session_id)

        minion_eid = create_combat_npc(self.ws_server.world, 100, 100, "time_a")
        self.ws_server.world.add_component(minion_eid, MapLocation(map_file))

        mob_eid = create_enemy(self.ws_server.world, 100, 110, race="Lobo")  # 10 tiles do minion
        self.ws_server.world.add_component(mob_eid, MapLocation(map_file))

        await self._run_ticks_async(3)
        self.assertNotIn(mob_eid, session_a.known_eids,
                         "mob a 10 tiles do minion aliado (raio 8) não deveria ter aparecido pra A")

    async def test_times_inimigos_mesma_instancia_sem_visao_cruzada(self):
        """Time inimigo na MESMA instância não concede visão — bucket
        separado por (map_file, faction_id)."""
        from engine.components import Faction

        session_a, _ = await fake_login(self.mgr, "s1", "avs_enemy_a", 10, 10)
        session_c, _ = await fake_login(self.mgr, "s2", "avs_enemy_c", 100, 100)
        self.ws_server.world.add_component(session_a.entity_id, Faction(faction_id="time_a"))
        self.ws_server.world.add_component(session_c.entity_id, Faction(faction_id="time_b"))

        centers = self.mgr._compute_ally_vision_centers()
        self.assertNotIn(session_a.entity_id, centers,
                         "time_a sozinho (sem aliado de time_a) não deveria ter centros")
        c_positions = [(tx, ty) for tx, ty, _ in centers.get(session_c.entity_id, [])]
        self.assertNotIn((100, 100), c_positions)

    async def test_mesma_faccao_instancias_diferentes_sem_visao_cruzada(self):
        """Mesma Faction, mas instâncias/mapas diferentes (ex: 2 partidas de
        arena distintas reaproveitando o mesmo id de time) — sem visão
        cruzada, já que o bucket inclui o map_file."""
        from engine.components import Faction

        session_a, _ = await fake_login(self.mgr, "s1", "avs_inst_a", 10, 10)
        session_b, _ = await fake_login(self.mgr, "s2", "avs_inst_b", 100, 100)
        self.ws_server.world.add_component(session_a.entity_id, Faction(faction_id="time_a"))
        self.ws_server.world.add_component(session_b.entity_id, Faction(faction_id="time_a"))
        # Simula B numa instância DIFERENTE (mesmo template/faction_id, outro match).
        self.ws_server._player_maps[session_b.session_id] = "maps/arena_poco_negro.csv::99"

        centers = self.mgr._compute_ally_vision_centers()
        self.assertEqual(centers.get(session_a.entity_id, []), [],
                         "aliado em instância diferente não deveria contribuir centro de visão")

    async def test_sessions_in_aoi_inclui_via_visao_de_time(self):
        """Broadcast direto (skill/som/chat de proximidade) perto de um
        teammate, fora do próprio AOI de A, ainda deve alcançar a sessão de
        A — pedido do usuário: chat de proximidade também viaja pela visão
        de time, sem exceção/flag."""
        from engine.components import Faction

        session_a, _ = await fake_login(self.mgr, "s1", "avs_bc_a", 10, 10)
        session_b, _ = await fake_login(self.mgr, "s2", "avs_bc_b", 100, 100)
        self.ws_server.world.add_component(session_a.entity_id, Faction(faction_id="time_a"))
        self.ws_server.world.add_component(session_b.entity_id, Faction(faction_id="time_a"))

        self.mgr._ally_vision_centers = self.mgr._compute_ally_vision_centers()
        map_file = self.ws_server.get_player_map(session_a.session_id)
        result = self.mgr._sessions_in_aoi(101, 100, map_file)   # evento a 1 tile de B
        result_sids = {s.session_id for s in result}
        self.assertIn(session_a.session_id, result_sids,
                     "A deveria receber broadcast perto do teammate B via visão de time")

    async def test_camuflagem_ainda_bloqueia_mesmo_visivel_via_aliado(self):
        """Player inimigo camuflado (Camuflagem, is_visible=False) perto de
        um teammate continua invisível pra A — visão de time não bypassa
        _can_see (checado por entidade, no ponto de inclusão, independente
        de qual centro cobriria a posição).

        Deixa 1 tick "assentar" o login de D ANTES de camuflar/estabelecer
        o time — nessa janela D só fica conhecido de B (perto o bastante
        do próprio AOI dele), nunca de A (longe, sem visão de time ainda).
        Só DEPOIS camufla D e atacha Faction em A/B — a única forma de A
        vir a descobrir D dali em diante é o sweep de "outros players"
        (roda todo tick, sem depender de delta), que checa _can_see.
        Camuflar ANTES desse tick de assentamento esbarra num gap
        PRÉ-EXISTENTE e não relacionado a esta feature: o bloco de
        `deltas["spawned"]` em _build_update_for_session não chama
        _can_see (só checa in_aoi) — um player recém-logado entra em
        known_eids de quem já está por perto NO MESMO tick do login,
        camuflado ou não. Fora de escopo consertar aqui."""
        from engine.components import Faction, CombatState

        session_a, _ = await fake_login(self.mgr, "s1", "avs_cam_a", 10, 10)
        session_b, _ = await fake_login(self.mgr, "s2", "avs_cam_b", 100, 100)
        session_d, _ = await fake_login(self.mgr, "s3", "avs_cam_d", 103, 100)

        await self._run_ticks_async(2)   # assenta o spawn de D (só B o conhece)
        self.assertNotIn(session_d.entity_id, session_a.known_eids,
                         "setup: A não deveria conhecer D ainda (sem visão de time)")

        cst = self.ws_server.world.get_component(session_d.entity_id, CombatState)
        cst.is_visible = False
        self.ws_server.world.add_component(session_a.entity_id, Faction(faction_id="time_a"))
        self.ws_server.world.add_component(session_b.entity_id, Faction(faction_id="time_a"))

        await self._run_ticks_async(3)
        self.assertNotIn(session_d.entity_id, session_a.known_eids,
                         "player camuflado não deveria aparecer pra A mesmo via visão de B")

    async def test_known_eids_nao_oscila_quando_um_aliado_sai_mas_outro_cobre(self):
        """Aliado B some do range mas aliado C ainda cobre M — M não deve
        despawnar (os centros são recalculados do zero todo tick a partir
        do estado ATUAL, nenhum center é 'lembrado' de tick anterior)."""
        from engine.components import Faction, MapLocation, TileMovement
        from engine.entity_factory import create_enemy

        session_a, _ = await fake_login(self.mgr, "s1", "avs_osc_a", 10, 10)
        session_b, _ = await fake_login(self.mgr, "s2", "avs_osc_b", 100, 100)
        session_c, _ = await fake_login(self.mgr, "s3", "avs_osc_c", 100, 100)
        for sid in (session_a, session_b, session_c):
            self.ws_server.world.add_component(sid.entity_id, Faction(faction_id="time_a"))
        map_file = self.ws_server.get_player_map(session_a.session_id)

        mob_eid = create_enemy(self.ws_server.world, 103, 100, race="Lobo")
        self.ws_server.world.add_component(mob_eid, MapLocation(map_file))

        await self._run_ticks_async(3)
        self.assertIn(mob_eid, session_a.known_eids, "setup: mob deveria ter aparecido pra A")

        # B se afasta pra bem longe (sai de qualquer cobertura) — C continua perto do mob.
        from tests.helpers import set_entity_tile
        set_entity_tile(self.ws_server, session_b.entity_id, 500, 500)
        self.ws_server._moved_this_tick.append({
            "eid": session_b.entity_id, "tx": 500, "ty": 500,
            "from_tx": 100, "from_ty": 100,
        })
        await self._run_ticks_async(3)

        self.assertIn(mob_eid, session_a.known_eids,
                     "mob não deveria despawnar: C ainda cobre, mesmo com B fora de range")

    async def test_faction_removida_no_fim_da_partida_remove_visao_no_proximo_tick(self):
        """Faction removida (fim de partida, mesmo padrão de
        match_processor.py) — aliado deixa de contribuir centro de visão
        no tick seguinte, sem entrada 'presa' de uma partida encerrada."""
        from engine.components import Faction

        session_a, _ = await fake_login(self.mgr, "s1", "avs_end_a", 10, 10)
        session_b, _ = await fake_login(self.mgr, "s2", "avs_end_b", 100, 100)
        fac_a = Faction(faction_id="time_a")
        fac_b = Faction(faction_id="time_a")
        self.ws_server.world.add_component(session_a.entity_id, fac_a)
        self.ws_server.world.add_component(session_b.entity_id, fac_b)

        centers_before = self.mgr._compute_ally_vision_centers()
        self.assertIn(session_a.entity_id, centers_before)

        self.ws_server.world.remove_component(session_b.entity_id, Faction)
        centers_after = self.mgr._compute_ally_vision_centers()
        self.assertNotIn(session_a.entity_id, centers_after,
                         "aliado sem Faction (partida encerrada) não deveria mais contribuir visão")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
