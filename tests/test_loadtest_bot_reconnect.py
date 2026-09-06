"""
tests/test_loadtest_bot_reconnect.py — bug real (06/09/2026, ver
PROBLEMAS_ARQUITETURA.md §113): `LoadBot.run()` nunca fechava a conexão
WebSocket da tentativa anterior antes de reconectar. Sob contenção de
SQLite (rajada de REGISTER/LOGIN), o `recv_one()` do lado do bot (timeout
de 10s, `tools/loadtest/protocol_client.py`) podia desistir ANTES do
servidor terminar de processar — a conexão velha ficava pendurada até o
ping_timeout do servidor (60s, `server/main.py`), e se o LOGIN dela
completasse depois, virava uma sessão "zumbi" que bloqueia toda
reconexão seguinte com HTTP LOGIN_ERROR "already_online"
(`server/session.py::_handle_login`) — amplificando a mesma contenção
que causou o timeout original (medido: 1181 reconexões pra 250 bots).

Este teste não abre socket real — substitui `LoadBot._login_cycle` por
um fake que simula "1ª tentativa falha" e verifica que o cliente da 1ª
tentativa teve `close()` chamado ANTES da 2ª tentativa começar.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.loadtest.bot import LoadBot


class _FakeClient:
    """Substitui ProtocolClient — só rastreia se/quando close() foi chamado."""
    instances = []

    def __init__(self):
        self.close_called = False
        _FakeClient.instances.append(self)

    async def close(self):
        self.close_called = True

    async def messages(self):
        # Async generator vazio — recv_loop encerra na hora, sem exceção.
        return
        yield  # pragma: no cover — nunca executa, só faz disto um generator


class TestLoadBotReconnectClosesStaleClient(unittest.TestCase):

    def setUp(self):
        _FakeClient.instances.clear()

    def test_reconnect_fecha_cliente_da_tentativa_anterior(self):
        bot = LoadBot(
            index=0, role="world", host="127.0.0.1", port=9999, seed=1,
            bot_eids=set(), think_min_s=999.0, think_max_s=999.0,
            requeue=False, stats={},
        )

        attempt = {"n": 0}

        async def fake_login_cycle(self_bot):
            self_bot.client = _FakeClient()
            attempt["n"] += 1
            if attempt["n"] == 1:
                # Simula o timeout de LOGIN sob contenção de SQLite —
                # `_login_cycle` real levanta RuntimeError igual a este
                # quando `recv_one()` devolve None.
                raise RuntimeError("LOGIN falhou: simulated DB contention timeout")
            # 2ª tentativa "sucede" o suficiente pra sair do teste.
            self_bot.eid = 42
            self_bot._stop.set()

        bot._login_cycle = fake_login_cycle.__get__(bot, LoadBot)
        # Backoff instantâneo — não precisamos esperar o jitter real no teste.
        bot._reconnect_backoff = lambda exc: asyncio.sleep(0)

        asyncio.run(asyncio.wait_for(bot.run(), timeout=5.0))

        self.assertEqual(attempt["n"], 2, "esperava exatamente 2 tentativas de login")
        self.assertEqual(len(_FakeClient.instances), 2)
        first_client, second_client = _FakeClient.instances
        self.assertTrue(
            first_client.close_called,
            "cliente da 1ª tentativa (que falhou) nunca foi fechado — "
            "conexão fica pendurada no servidor até o ping_timeout (60s), "
            "podendo virar sessão zumbi que bloqueia reconexão (§113)")
        self.assertTrue(second_client.close_called,
                        "cliente final também deve ser fechado ao bot parar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
