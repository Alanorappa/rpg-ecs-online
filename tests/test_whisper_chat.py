"""
tests/test_whisper_chat.py — sussurro por nome (02/10/2026, pedido do
usuário: "/w Nome mensagem", entrega direta sem exigir mesmo mapa/
lugar, mesmo comando do WoW) + clicar no nome pra responder.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((100, 100))

import unittest
from unittest import mock


# ── Servidor: roteamento do whisper em _handle_chat ─────────────────────────

class TestServerWhisperRouting(unittest.TestCase):
    """`server/session.py::_handle_chat`, ramo `channel == "whisper"` --
    testado isolado (sem DB real: usa objetos `Session`/`SessionManager`
    mínimos, só os atributos que o trecho de código realmente lê)."""

    def _make_mgr_with_sessions(self):
        from server.session import SessionManager
        mgr = SessionManager.__new__(SessionManager)  # bypassa __init__ pesado
        mgr._sessions = {}
        return mgr

    class _FakeSession:
        def __init__(self, sid, name, eid):
            self.session_id    = sid
            self.display_name  = name
            self.entity_id     = eid
            self.authenticated = True
            self.sent          = []

        async def send(self, msg_type, payload):
            self.sent.append((msg_type, payload))

    def test_sussurro_entrega_na_sessao_certa_por_nome(self):
        import asyncio
        from server.session import SessionManager
        from shared.messages import MsgType

        mgr = self._make_mgr_with_sessions()
        sender = self._FakeSession("s1", "Remetente", 10)
        target = self._FakeSession("s2", "Destinatario", 20)
        mgr._sessions = {"s1": sender, "s2": target}

        asyncio.run(SessionManager._handle_chat(mgr, sender, {
            "text": "oi, tudo bem?", "channel": "whisper", "target_name": "Destinatario",
        }, 0))

        self.assertEqual(len(target.sent), 1, "destinatário deveria receber exatamente 1 mensagem")
        mt, payload = target.sent[0]
        self.assertEqual(mt, MsgType.CHAT_MESSAGE)
        self.assertEqual(payload["sender"], "Remetente")
        self.assertEqual(payload["text"], "oi, tudo bem?")
        self.assertEqual(payload["channel"], "whisper")
        self.assertNotIn("to", payload, "cópia de CHEGADA não deve ter 'to' -- isso é só do eco de saída")

        self.assertEqual(len(sender.sent), 1, "remetente deveria receber o eco da própria mensagem")
        mt2, payload2 = sender.sent[0]
        self.assertEqual(payload2["to"], "Destinatario")
        self.assertEqual(payload2["sender"], "Remetente")

    def test_sussurro_busca_nome_sem_diferenciar_maiusculas(self):
        import asyncio
        from server.session import SessionManager

        mgr = self._make_mgr_with_sessions()
        sender = self._FakeSession("s1", "Remetente", 10)
        target = self._FakeSession("s2", "Destinatario", 20)
        mgr._sessions = {"s1": sender, "s2": target}

        asyncio.run(SessionManager._handle_chat(mgr, sender, {
            "text": "oi", "channel": "whisper", "target_name": "destinatario",  # minúsculo
        }, 0))

        self.assertEqual(len(target.sent), 1)

    def test_sussurro_pra_alvo_offline_avisa_so_o_remetente(self):
        import asyncio
        from server.session import SessionManager

        mgr = self._make_mgr_with_sessions()
        sender = self._FakeSession("s1", "Remetente", 10)
        mgr._sessions = {"s1": sender}

        asyncio.run(SessionManager._handle_chat(mgr, sender, {
            "text": "oi", "channel": "whisper", "target_name": "NaoExiste",
        }, 0))

        self.assertEqual(len(sender.sent), 1)
        _mt, payload = sender.sent[0]
        self.assertEqual(payload["sender"], "Sistema")
        self.assertIn("NaoExiste", payload["text"])
        self.assertNotIn("to", payload)

    def test_sussurro_funciona_com_alvo_em_outra_sessao_qualquer(self):
        """Confirma o pedido central do usuário -- a entrega é por SESSÃO
        AUTENTICADA, nunca filtra por mapa/posição/AOI (diferente do
        chat normal, que usa _broadcast_aoi_from_session)."""
        import asyncio
        from server.session import SessionManager

        mgr = self._make_mgr_with_sessions()
        sender = self._FakeSession("s1", "A", 1)
        target = self._FakeSession("s2", "B", 2)
        mgr._sessions = {"s1": sender, "s2": target}
        # Nunca chamado nada de AOI/mapa -- se o código do whisper
        # tentasse usar _sessions_in_aoi/_broadcast_aoi_from_session,
        # este mock ausente já faria o teste explodir.
        with mock.patch.object(SessionManager, "_broadcast_aoi_from_session",
                               side_effect=AssertionError("não deveria usar AOI pra whisper")):
            asyncio.run(SessionManager._handle_chat(mgr, sender, {
                "text": "oi", "channel": "whisper", "target_name": "B",
            }, 0))
        self.assertEqual(len(target.sent), 1)


# ── Cliente: parsing do comando "/w" ─────────────────────────────────────────

class TestWhisperChatCommand(unittest.TestCase):

    def _make_engine(self):
        from client.chat_handlers import ChatHandlers
        from collections import deque

        class _FakeEngine(ChatHandlers):
            pass

        eng = _FakeEngine()
        eng._chat_local = deque(maxlen=500)
        eng._chat_world = deque(maxlen=500)
        eng._net = mock.Mock()
        return eng

    def test_texto_sem_barra_w_nao_e_consumido(self):
        eng = self._make_engine()
        self.assertFalse(eng._try_handle_whisper_chat_command("mensagem normal"))
        eng._net.send.assert_not_called()

    def test_comando_valido_manda_chat_send_com_os_campos_certos(self):
        eng = self._make_engine()
        consumed = eng._try_handle_whisper_chat_command("/w Fulano tudo bem?")
        self.assertTrue(consumed)
        from shared.messages import MsgType
        eng._net.send.assert_called_once_with(MsgType.CHAT_SEND, {
            "text": "tudo bem?", "channel": "whisper", "target_name": "Fulano",
        })

    def test_comando_maiusculo_tambem_funciona(self):
        eng = self._make_engine()
        self.assertTrue(eng._try_handle_whisper_chat_command("/W Fulano oi"))
        eng._net.send.assert_called_once()

    def test_sem_mensagem_mostra_aviso_local_sem_mandar_pro_servidor(self):
        eng = self._make_engine()
        consumed = eng._try_handle_whisper_chat_command("/w Fulano")
        self.assertTrue(consumed)
        eng._net.send.assert_not_called()
        self.assertEqual(len(eng._chat_local), 1)
        self.assertEqual(eng._chat_local[0][0], "Sistema")

    def test_sem_nome_nenhum_mostra_aviso_local(self):
        eng = self._make_engine()
        consumed = eng._try_handle_whisper_chat_command("/w")
        self.assertTrue(consumed)
        eng._net.send.assert_not_called()

    def test_comando_valido_grava_alvo_fixo(self):
        """Sussurro FIXO (03/10/2026, pedido do usuário, estilo WoW)."""
        eng = self._make_engine()
        eng._chat_whisper_target = None
        eng._try_handle_whisper_chat_command("/w Fulano oi")
        self.assertEqual(eng._chat_whisper_target, "Fulano")

    def test_sem_mensagem_nao_grava_alvo_fixo(self):
        eng = self._make_engine()
        eng._chat_whisper_target = None
        eng._try_handle_whisper_chat_command("/w Fulano")
        self.assertIsNone(eng._chat_whisper_target,
                          "comando incompleto não deve ativar sussurro fixo")

    def test_novo_w_troca_o_alvo_fixo(self):
        eng = self._make_engine()
        eng._chat_whisper_target = "Antigo"
        eng._try_handle_whisper_chat_command("/w Novo oi")
        self.assertEqual(eng._chat_whisper_target, "Novo")


class TestSayChatCommand(unittest.TestCase):
    """"/s" (03/10/2026, pedido do usuário, mesma ideia do WoW) -- sai
    do sussurro fixo, opcionalmente mandando uma mensagem no canal
    normal junto."""

    def _make_engine(self, chat_tab: str = "local"):
        from client.chat_handlers import ChatHandlers
        from collections import deque

        class _FakeEngine(ChatHandlers):
            pass

        eng = _FakeEngine()
        eng._chat_local = deque(maxlen=500)
        eng._chat_world = deque(maxlen=500)
        eng._chat_tab = chat_tab
        eng._chat_whisper_target = "Fulano"
        eng._net = mock.Mock()
        return eng

    def test_texto_sem_barra_s_nao_e_consumido(self):
        eng = self._make_engine()
        self.assertFalse(eng._try_handle_say_chat_command("mensagem normal"))
        self.assertEqual(eng._chat_whisper_target, "Fulano", "não deve alterar nada se não casou o comando")

    def test_s_sozinho_limpa_alvo_e_nao_manda_nada(self):
        eng = self._make_engine()
        consumed = eng._try_handle_say_chat_command("/s")
        self.assertTrue(consumed)
        self.assertIsNone(eng._chat_whisper_target)
        eng._net.send.assert_not_called()
        self.assertEqual(len(eng._chat_local), 1)
        self.assertEqual(eng._chat_local[0][0], "Sistema")

    def test_s_com_mensagem_limpa_alvo_e_manda_no_canal_local(self):
        eng = self._make_engine(chat_tab="local")
        consumed = eng._try_handle_say_chat_command("/s oi pessoal")
        self.assertTrue(consumed)
        self.assertIsNone(eng._chat_whisper_target)
        from shared.messages import MsgType
        eng._net.send.assert_called_once_with(MsgType.CHAT_SEND,
                                              {"text": "oi pessoal", "channel": "local"})

    def test_s_com_mensagem_na_aba_mundial_manda_pro_mundial(self):
        eng = self._make_engine(chat_tab="world")
        eng._try_handle_say_chat_command("/s oi mundo")
        from shared.messages import MsgType
        eng._net.send.assert_called_once_with(MsgType.CHAT_SEND,
                                              {"text": "oi mundo", "channel": "world"})


class TestStickyWhisperViaSendChatMessage(unittest.TestCase):
    """Integração via `_send_chat_message` de verdade -- confirma o
    pedido central do usuário: depois de "/w Nome msg", texto PLANO
    (sem comando) continua indo pro MESMO alvo, até outro "/w" trocar
    ou "/s" sair."""

    def _make_engine(self):
        from client.chat_handlers import ChatHandlers
        from collections import deque

        class _FakeEngine(ChatHandlers):
            # Stubs dos outros comandos de chat (party/arena/bg) --
            # irrelevantes aqui, sempre "não consumido".
            def _try_handle_party_chat_command(self, text): return False
            def _try_handle_arena_chat_command(self, text): return False
            def _try_handle_bg_forfeit_chat_command(self, text): return False
            def _try_handle_bg_chat_command(self, text): return False

        eng = _FakeEngine()
        eng._chat_local  = deque(maxlen=500)
        eng._chat_world  = deque(maxlen=500)
        eng._chat_tab    = "local"
        eng._chat_active = True
        eng._chat_whisper_target = None
        eng._net = mock.Mock()
        return eng

    def _send(self, eng, text: str) -> None:
        eng._chat_text = text
        eng._send_chat_message()

    def test_fluxo_completo_w_depois_texto_plano_depois_s(self):
        from shared.messages import MsgType
        eng = self._make_engine()

        self._send(eng, "/w Fulano oi")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND,
                                         {"text": "oi", "channel": "whisper", "target_name": "Fulano"})

        # Texto PLANO -- sem nenhum comando -- continua pro MESMO alvo.
        self._send(eng, "como você está?")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND, {
            "text": "como você está?", "channel": "whisper", "target_name": "Fulano",
        })

        # Troca de alvo com um novo "/w".
        self._send(eng, "/w Outro oi")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND,
                                         {"text": "oi", "channel": "whisper", "target_name": "Outro"})
        self._send(eng, "tudo bem?")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND, {
            "text": "tudo bem?", "channel": "whisper", "target_name": "Outro",
        })

        # "/s" sai do sussurro fixo -- texto plano seguinte volta pro local.
        self._send(eng, "/s")
        self.assertIsNone(eng._chat_whisper_target)
        self._send(eng, "oi geral")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND, {"text": "oi geral", "channel": "local"})

    def test_sem_nunca_ter_sussurrado_texto_plano_vai_pro_canal_normal(self):
        from shared.messages import MsgType
        eng = self._make_engine()
        self._send(eng, "mensagem qualquer")
        eng._net.send.assert_called_once_with(MsgType.CHAT_SEND,
                                              {"text": "mensagem qualquer", "channel": "local"})


# ── Cliente: composição do rótulo/reply_target ao receber CHAT_MESSAGE ──────

class TestWhisperMessageLabel(unittest.TestCase):

    def _make_engine(self):
        from client.network_handlers import NetworkHandlers
        from client.chat_handlers import ChatHandlers
        from engine.world import World
        from collections import deque

        class _FakeEngine(NetworkHandlers, ChatHandlers):
            def _resolve_chat_sender_entity(self, sender_eid, sender_name):
                return -1

        eng = _FakeEngine()
        eng.world = World()
        eng.player_entity = eng.world.create_entity()
        eng._chat_local = deque(maxlen=500)
        eng._chat_world = deque(maxlen=500)
        return eng

    def test_sussurro_recebido_mostra_x_sussurra(self):
        eng = self._make_engine()
        eng._handle_msg_chat_message({
            "sender": "Fulano", "eid": 5, "text": "oi",
            "channel": "whisper", "color": [150, 210, 255],
        })
        label, text, color, reply_target = eng._chat_local[0]
        self.assertEqual(label, "Fulano sussurra")
        self.assertEqual(reply_target, "Fulano")
        self.assertEqual(color, (150, 210, 255))

    def test_eco_de_saida_mostra_para_fulano(self):
        """03/10/2026, pedido do usuário -- rótulo completo ("Você
        sussurra para Fulano") ficava grande demais no chat; só "para
        Fulano" (reply_target continua o nome puro, sem mudança)."""
        eng = self._make_engine()
        eng._handle_msg_chat_message({
            "sender": "EuMesmo", "eid": 1, "text": "oi",
            "channel": "whisper", "color": [150, 210, 255], "to": "Fulano",
        })
        label, text, color, reply_target = eng._chat_local[0]
        self.assertEqual(label, "para Fulano")
        self.assertEqual(reply_target, "Fulano")

    def test_mensagem_normal_local_reply_target_e_o_sender(self):
        eng = self._make_engine()
        eng._handle_msg_chat_message({
            "sender": "Fulano", "eid": 5, "text": "oi", "channel": "local",
        })
        label, text, color, reply_target = eng._chat_local[0]
        self.assertEqual(label, "Fulano")
        self.assertEqual(reply_target, "Fulano")

    def test_mensagem_de_sistema_sem_reply_target(self):
        eng = self._make_engine()
        eng._handle_msg_chat_message({
            "sender": "Sistema", "eid": -1, "text": "aviso", "channel": "local",
        })
        _label, _text, _color, reply_target = eng._chat_local[0]
        self.assertIsNone(reply_target)

    def test_mensagem_mundial_vai_pra_chat_world(self):
        eng = self._make_engine()
        eng._handle_msg_chat_message({
            "sender": "Fulano", "eid": 5, "text": "oi", "channel": "world",
        })
        self.assertEqual(len(eng._chat_world), 1)
        self.assertEqual(len(eng._chat_local), 0)


# ── Cliente: clicar no nome abre resposta pré-preenchida ────────────────────

class TestChatNameClickOpensReply(unittest.TestCase):

    def _make_engine(self, own_name: str = "EuMesmo"):
        from client.chat_handlers import ChatHandlers
        from engine.world import World
        from engine.components import CharacterStats
        from collections import deque

        class _FakeEngine(ChatHandlers):
            pass

        eng = _FakeEngine()
        eng.world = World()
        eng.player_entity = eng.world.create_entity()
        eng.world.add_component(eng.player_entity, CharacterStats(name=own_name))
        eng.screen = pygame.display.get_surface()
        eng.font_sm = pygame.font.Font(None, 20)
        eng._u = lambda v: v
        eng._chat_active = False
        eng._chat_tab = "local"
        eng._chat_local = deque(maxlen=500)
        eng._chat_world = deque(maxlen=500)
        eng._chat_scroll = {"local": 0, "world": 0, "combat": 0}
        eng._chat_text = ""
        return eng

    def _click_on_name(self, eng, label: str, msg: str = "mensagem de teste"):
        """Simula clique bem no início da área de mensagens -- a 1ª
        linha mostrada é sempre a MAIS RECENTE adicionada (scroll=0
        ancorado no fundo), então com 1 entrada só ela ocupa a última
        linha visível; clicamos perto do topo do texto dela usando a
        geometria real de `_chat_layout()`."""
        eng._chat_local.append((label, msg, (255, 255, 255), label))
        L = eng._chat_layout()
        msg_area = L["msg_area"]
        line_h = eng._u(__import__("ui.ui_sizes", fromlist=["UI"]).UI.CHAT_LINE_H)
        # única linha existente -- sempre a primeira mostrada.
        y = msg_area.y + line_h // 2
        x = msg_area.x + 2   # bem no início do nome
        return eng._try_handle_chat_name_click(x, y, msg_area)

    def test_clique_no_nome_abre_input_com_comando_w_preenchido(self):
        eng = self._make_engine()
        self._click_on_name(eng, "Fulano")
        self.assertTrue(eng._chat_active)
        self.assertEqual(eng._chat_text, "/w Fulano ")

    def test_clique_no_proprio_nome_nao_faz_nada(self):
        eng = self._make_engine(own_name="EuMesmo")
        self._click_on_name(eng, "EuMesmo")
        self.assertFalse(eng._chat_active)
        self.assertEqual(eng._chat_text, "")

    def test_clique_fora_da_regiao_do_nome_nao_faz_nada(self):
        eng = self._make_engine()
        eng._chat_local.append(("Fulano", "mensagem bem longa de teste", (255, 255, 255), "Fulano"))
        L = eng._chat_layout()
        msg_area = L["msg_area"]
        # Clica no CANTO DIREITO da área de mensagens, longe de onde o
        # nome "Fulano:" é desenhado (que começa em msg_area.x).
        eng._try_handle_chat_name_click(msg_area.right - 2, msg_area.y + 2, msg_area)
        self.assertFalse(eng._chat_active)

    def test_clique_na_aba_combate_nunca_abre_resposta(self):
        eng = self._make_engine()
        eng._chat_tab = "combat"
        eng._chat_local.append(("Fulano", "oi", (255, 255, 255), "Fulano"))
        L = eng._chat_layout()
        msg_area = L["msg_area"]
        eng._try_handle_chat_name_click(msg_area.x + 2, msg_area.y + 2, msg_area)
        self.assertFalse(eng._chat_active)


if __name__ == "__main__":
    unittest.main(verbosity=2)
