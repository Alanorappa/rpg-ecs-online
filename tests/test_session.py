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

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob
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

    async def test_second_player_in_known_eids_of_first(self):
        """Quando B loga perto de A, o eid de B deve ir para known_eids de A."""
        session_a, _ = await fake_login(self.mgr, "s1", "user_ka", 115, 389)
        session_b, _ = await fake_login(self.mgr, "s2", "user_kb", 117, 389)
        self.assertIn(session_b.entity_id, session_a.known_eids,
                      "Eid de B não está em known_eids de A")

    async def test_first_player_receives_entity_spawn_when_b_logs_in(self):
        """Quando B loga perto de A, A deve receber ENTITY_SPAWN de B."""
        session_a, fw_a = await fake_login(self.mgr, "s1", "user_spa", 115, 389)
        fw_a.sent.clear()   # limpa mensagens do próprio login de A
        _, _ = await fake_login(self.mgr, "s2", "user_spb", 117, 389)
        spawns = get_msgs_of_type(fw_a, MsgType.ENTITY_SPAWN)
        self.assertGreater(len(spawns), 0, "A não recebeu ENTITY_SPAWN quando B logou")


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
        mob_tm = self.ws_server.world.get_component(mob_eid, TileMovement)
        ptm    = self.ws_server.world.get_component(player_eid, TileMovement)
        mob_tm.current_tile_x = ptm.current_tile_x + 1
        mob_tm.current_tile_y = ptm.current_tile_y
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
        mob_cs.current_hp = 1
        player_eid = session_a.entity_id
        mob_tm = self.ws_server.world.get_component(mob_eid, TileMovement)
        ptm    = self.ws_server.world.get_component(player_eid, TileMovement)
        mob_tm.current_tile_x = ptm.current_tile_x + 1
        mob_tm.current_tile_y = ptm.current_tile_y
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
        mob_tm = self.ws_server.world.get_component(mob_eid, TileMovement)
        ptm    = self.ws_server.world.get_component(player_eid, TileMovement)
        mob_tm.current_tile_x = ptm.current_tile_x + 1
        mob_tm.current_tile_y = ptm.current_tile_y
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
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
