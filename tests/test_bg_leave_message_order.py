"""tests/test_bg_leave_message_order.py — server/session.py::_handle_chat
(branch "/testbg").

BUG REAL relatado pelo usuário (03/08/2026): "estou perdendo os itens da
bag quando saio da BG, parece que o jogo está persistindo a bag vazia da
instância pra fora da instância".

Causa raiz: o ZONE_CHANGE de "/testbg leave" era enviado IMEDIATAMENTE
(fora do dispatch batched por-tick), enquanto o STATS_UPDATE com o
Inventory/Equipment/CharacterStats REAIS — restaurados por
exit_normalized_progression via WorldServer.queue_stats_update — só era
despachado no PRÓXIMO tick (`_dispatch_tick_deltas` drena
`_pending_stats_updates`). O cliente processa ZONE_CHANGE →
`_do_transition` (game.py), que dispara autosave (SAVE_STATE) no fim —
com o Inventory local AINDA "de instância" (vazio/6 slots), porque o
STATS_UPDATE corretivo ainda não tinha chegado. Como
exit_normalized_progression já rodou no SERVIDOR antes do ZONE_CHANGE
ser enviado, `is_in_normalized_progression` já é False quando esse
SAVE_STATE prematuro chega — `_persist_character` não bloqueia, e a bag
vazia é gravada no banco por cima da real.

Fix: `SessionManager._flush_stats_updates()` chamado ANTES do
ZONE_CHANGE no branch "/testbg" de `_handle_chat` — garante que o
STATS_UPDATE com a restauração real sempre chega ao cliente ANTES da
troca de mapa.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.test_session import make_session_manager, fake_login, get_messages
from shared.messages import encode, MsgType
from server import debug_battleground as bg


class TestBattlegroundLeaveMessageOrder(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        self.session, self.fw = await fake_login(
            self.mgr, "s1", "bgordertest", class_id="guerreiro")
        # Item real e distinto na bag ANTES de entrar na instância — prova
        # que o STATS_UPDATE pré-ZONE_CHANGE carrega a bag REAL restaurada
        # (não uma vazia/de instância).
        from engine.components import Inventory, Item
        inv = self.ws_server.world.get_component(self.session.entity_id, Inventory)
        inv.items = [Item("Espada Real de Teste", "weapon", "mainhand")]

    async def asyncTearDown(self):
        bg._state["members"]        = set()
        bg._state["stat_snapshots"] = {}
        bg._state["last_kda_sent"]  = {}
        bg._state["loaded"]         = False
        bg._state["gate_deadline"]  = None

    async def _send_chat(self, text: str) -> None:
        await self.mgr.on_message(self.session, encode(MsgType.CHAT_SEND, {
            "text": text, "channel": "local",
        }))

    async def test_stats_update_com_bag_real_chega_antes_do_zone_change_ao_sair(self):
        await self._send_chat("/testbg a")
        self.fw.sent.clear()

        await self._send_chat("/testbg leave")

        messages = get_messages(self.fw)
        types_in_order = [mt for mt, _, _, _ in messages]
        self.assertIn(MsgType.ZONE_CHANGE, types_in_order,
                     "deveria ter mandado ZONE_CHANGE ao sair")

        stats_idx = None
        zone_idx  = None
        for i, (mt, payload, _, _) in enumerate(messages):
            if mt == MsgType.STATS_UPDATE and "inv_snapshot" in payload:
                stats_idx = i
            if mt == MsgType.ZONE_CHANGE and zone_idx is None:
                zone_idx = i

        self.assertIsNotNone(stats_idx,
                             "STATS_UPDATE com inv_snapshot deveria ter sido enviado")
        self.assertLess(stats_idx, zone_idx,
                        "STATS_UPDATE (bag real restaurada) precisa chegar ANTES "
                        "do ZONE_CHANGE — senão o autosave de _do_transition "
                        "salva a bag ainda 'de instância' por cima da real")

        _, stats_payload, _, _ = messages[stats_idx]
        item_names = [d.get("name") for d in stats_payload["inv_snapshot"]]
        self.assertIn("Espada Real de Teste", item_names,
                     "STATS_UPDATE pré-ZONE_CHANGE deveria já trazer a bag REAL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
