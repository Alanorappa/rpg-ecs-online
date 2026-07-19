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
    _create_character_sync(acc_id, username, class_id, tile_x, tile_y)

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
        _create_character_sync(acc_id, "user_ap_test", "guerreiro", 115, 389)

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
        _create_character_sync(acc_id, "user_fog_grande", "guerreiro", 115, 389)

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

        await self._run_ticks_async(5)

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


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
