"""
tests/test_protocol_validation.py — validação de payload na borda C→S
(item B4, PROBLEMAS_ARQUITETURA.md §11): mensagem malformada leva ERROR e
NUNCA chega ao handler; mensagem legítima passa como sempre.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import unittest

from shared.messages import MsgType, encode, validate_c2s
from tests.test_session import make_session_manager, fake_login, get_msgs_of_type


class TestValidateC2S(unittest.TestCase):
    """Unidade: a função pura de validação."""

    def test_mensagem_sem_schema_passa(self):
        self.assertIsNone(validate_c2s(MsgType.PING, {"qualquer": 1}))

    def test_campo_faltando(self):
        self.assertEqual(validate_c2s(MsgType.MOVE, {"tx": 1}), "missing_field:ty")

    def test_tipo_errado(self):
        self.assertEqual(validate_c2s(MsgType.MOVE, {"tx": 1, "ty": "398"}),
                         "bad_type:ty")
        self.assertEqual(validate_c2s(MsgType.CAST_SKILL, {"sid": 123}),
                         "bad_type:sid")
        self.assertEqual(validate_c2s(MsgType.INV_SYNC, {"inventory": "nao-lista"}),
                         "bad_type:inventory")

    def test_payload_nao_dict(self):
        self.assertEqual(validate_c2s(MsgType.MOVE, "lixo"), "payload_not_dict")

    def test_legitimos_passam(self):
        self.assertIsNone(validate_c2s(MsgType.MOVE, {"tx": 116, "ty": 389,
                                                       "from_tx": 115, "from_ty": 389}))
        self.assertIsNone(validate_c2s(MsgType.CAST_SKILL, {"sid": "golpe_poderoso",
                                                             "tid": 3}))
        self.assertIsNone(validate_c2s(MsgType.TRADE_SET_GOLD, {"amount": 50}))

    def test_talent_update_shape_real_do_cliente(self):
        """Regressão do warning real (15/07/2026): o cliente aninha tudo em
        'talents' (_send_talent_update) — o primeiro schema exigia
        'allocated' no topo e rejeitava alocação legítima de talento."""
        self.assertIsNone(validate_c2s(MsgType.TALENT_UPDATE, {"talents": {
            "chosen_build": "cavaleiro", "allocated": {"cav_explorador": 2},
            "available_points": 3}}))
        self.assertEqual(validate_c2s(MsgType.TALENT_UPDATE, {"allocated": {}}),
                         "missing_field:talents")


class TestEdgeRejection(unittest.IsolatedAsyncioTestCase):
    """Integração: a borda (on_message) rejeita antes do handler."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()

    async def test_malformada_leva_error_e_nao_executa(self):
        session, fw = await fake_login(self.mgr, "s1", "proto_a", 115, 389)
        from engine.components import TileMovement
        tm = self.ws_server.world.get_component(session.entity_id, TileMovement)
        tx0, ty0 = tm.current_tile_x, tm.current_tile_y

        fw.sent.clear()
        # MOVE sem ty (malformada) — não pode mover nem crashar
        await self.mgr.on_message(session, encode(MsgType.MOVE, {"tx": tx0 + 1}))
        errors = get_msgs_of_type(fw, MsgType.ERROR)
        self.assertTrue(any(str(e.get("reason", "")).startswith("invalid_payload:")
                            for e in errors), f"sem ERROR de payload: {errors}")
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (tx0, ty0),
                         "handler executou apesar do payload inválido")

    async def test_legitima_continua_passando(self):
        session, fw = await fake_login(self.mgr, "s1", "proto_b", 115, 389)
        from engine.components import TileMovement
        tm = self.ws_server.world.get_component(session.entity_id, TileMovement)
        tx0, ty0 = tm.current_tile_x, tm.current_tile_y

        fw.sent.clear()
        await self.mgr.on_message(session, encode(MsgType.MOVE, {
            "tx": tx0 + 1, "ty": ty0, "from_tx": tx0, "from_ty": ty0}))
        errors = get_msgs_of_type(fw, MsgType.ERROR)
        self.assertFalse(errors, f"mensagem legítima rejeitada: {errors}")
        self.assertEqual(tm.current_tile_x, tx0 + 1, "MOVE legítimo não aplicou")


if __name__ == "__main__":
    unittest.main(verbosity=2)
