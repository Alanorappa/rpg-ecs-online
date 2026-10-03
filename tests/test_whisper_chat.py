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


# ── Servidor: roteamento do chat de grupo em _handle_chat ───────────────────

class TestServerGroupChatRouting(unittest.TestCase):
    """`server/session.py::_handle_chat`, ramo `channel == "party"` --
    mesmo estilo de teste isolado do whisper, só que `world_server` é
    um fake com `get_party_members`/`get_session_id_for_player`
    controláveis (mesmo par já usado pelo broadcast de LOOT_UPDATE)."""

    class _FakeSession:
        def __init__(self, sid, name, eid):
            self.session_id    = sid
            self.display_name  = name
            self.entity_id     = eid
            self.authenticated = True
            self.sent          = []

        async def send(self, msg_type, payload):
            self.sent.append((msg_type, payload))

    class _FakeWorldServer:
        def __init__(self, members: list, sid_by_eid: dict):
            self._members = members
            self._sid_by_eid = sid_by_eid

        def get_party_members(self, eid):
            return self._members

        def get_session_id_for_player(self, eid):
            return self._sid_by_eid.get(eid)

    def _make_mgr(self, sessions: dict, members: list, sid_by_eid: dict):
        from server.session import SessionManager
        mgr = SessionManager.__new__(SessionManager)
        mgr._sessions = sessions
        mgr.world_server = self._FakeWorldServer(members, sid_by_eid)
        return mgr

    def test_mensagem_de_grupo_entrega_pra_todos_os_membros_inclusive_o_remetente(self):
        import asyncio
        from server.session import SessionManager

        a = self._FakeSession("s1", "A", 1)
        b = self._FakeSession("s2", "B", 2)
        c = self._FakeSession("s3", "C", 3)
        mgr = self._make_mgr(
            sessions={"s1": a, "s2": b, "s3": c},
            members=[1, 2, 3],
            sid_by_eid={1: "s1", 2: "s2", 3: "s3"},
        )

        asyncio.run(SessionManager._handle_chat(mgr, a, {
            "text": "vamos atacar", "channel": "party",
        }, 0))

        for sess in (a, b, c):
            self.assertEqual(len(sess.sent), 1, f"{sess.display_name} deveria receber a mensagem")
            _mt, payload = sess.sent[0]
            self.assertEqual(payload["sender"], "A")
            self.assertEqual(payload["text"], "vamos atacar")
            self.assertEqual(payload["channel"], "party")

    def test_sem_grupo_avisa_so_o_remetente(self):
        import asyncio
        from server.session import SessionManager

        a = self._FakeSession("s1", "A", 1)
        mgr = self._make_mgr(sessions={"s1": a}, members=[], sid_by_eid={})

        asyncio.run(SessionManager._handle_chat(mgr, a, {
            "text": "oi", "channel": "party",
        }, 0))

        self.assertEqual(len(a.sent), 1)
        _mt, payload = a.sent[0]
        self.assertEqual(payload["sender"], "Sistema")
        self.assertEqual(payload["channel"], "local")

    def test_grupo_de_1_membro_so_o_proprio_tambem_avisa(self):
        """`get_party_members` sempre inclui o próprio eid -- grupo "de
        1" (só o próprio) não é grupo de verdade."""
        import asyncio
        from server.session import SessionManager

        a = self._FakeSession("s1", "A", 1)
        mgr = self._make_mgr(sessions={"s1": a}, members=[1], sid_by_eid={1: "s1"})

        asyncio.run(SessionManager._handle_chat(mgr, a, {
            "text": "oi", "channel": "party",
        }, 0))

        self.assertEqual(len(a.sent), 1)
        self.assertEqual(a.sent[0][1]["sender"], "Sistema")

    def test_grupo_funciona_mesmo_com_membro_em_outra_sessao_qualquer(self):
        """Confirma o pedido central do usuário -- igual o whisper,
        nunca filtra por mapa/posição/AOI."""
        import asyncio
        from server.session import SessionManager

        a = self._FakeSession("s1", "A", 1)
        b = self._FakeSession("s2", "B", 2)
        mgr = self._make_mgr(
            sessions={"s1": a, "s2": b}, members=[1, 2], sid_by_eid={1: "s1", 2: "s2"})
        with mock.patch.object(SessionManager, "_broadcast_aoi_from_session",
                               side_effect=AssertionError("não deveria usar AOI pro chat de grupo")):
            asyncio.run(SessionManager._handle_chat(mgr, a, {
                "text": "oi", "channel": "party",
            }, 0))
        self.assertEqual(len(b.sent), 1)


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
        """Modo FIXO (03/10/2026, pedido do usuário, estilo WoW)."""
        eng = self._make_engine()
        eng._chat_sticky_mode = None
        eng._try_handle_whisper_chat_command("/w Fulano oi")
        self.assertEqual(eng._chat_sticky_mode, ("whisper", "Fulano"))

    def test_sem_mensagem_nao_grava_alvo_fixo(self):
        eng = self._make_engine()
        eng._chat_sticky_mode = None
        eng._try_handle_whisper_chat_command("/w Fulano")
        self.assertIsNone(eng._chat_sticky_mode,
                          "comando incompleto não deve ativar sussurro fixo")

    def test_novo_w_troca_o_alvo_fixo(self):
        eng = self._make_engine()
        eng._chat_sticky_mode = ("whisper", "Antigo")
        eng._try_handle_whisper_chat_command("/w Novo oi")
        self.assertEqual(eng._chat_sticky_mode, ("whisper", "Novo"))


class TestGroupChatCommand(unittest.TestCase):
    """"/p" ou "/grupo" (03/10/2026, pedido do usuário -- mesma ideia
    do "/w", mas pro grupo inteiro). Servidor resolve quem está no
    grupo -- cliente só manda `channel:"party"`."""

    def _make_engine(self):
        from client.chat_handlers import ChatHandlers
        from collections import deque

        class _FakeEngine(ChatHandlers):
            pass

        eng = _FakeEngine()
        eng._chat_local = deque(maxlen=500)
        eng._chat_world = deque(maxlen=500)
        eng._chat_sticky_mode = None
        eng._net = mock.Mock()
        return eng

    def test_texto_sem_barra_p_nao_e_consumido(self):
        eng = self._make_engine()
        self.assertFalse(eng._try_handle_group_chat_command("mensagem normal"))
        eng._net.send.assert_not_called()

    def test_p_com_mensagem_manda_channel_party_e_grava_alvo_fixo(self):
        eng = self._make_engine()
        consumed = eng._try_handle_group_chat_command("/p oi grupo")
        self.assertTrue(consumed)
        from shared.messages import MsgType
        eng._net.send.assert_called_once_with(MsgType.CHAT_SEND,
                                              {"text": "oi grupo", "channel": "party"})
        self.assertEqual(eng._chat_sticky_mode, ("party", None))

    def test_grupo_e_sinonimo_de_p(self):
        eng = self._make_engine()
        consumed = eng._try_handle_group_chat_command("/grupo oi")
        self.assertTrue(consumed)
        eng._net.send.assert_called_once()
        self.assertEqual(eng._chat_sticky_mode, ("party", None))

    def test_p_sozinho_so_ativa_o_modo_sem_mandar_nada(self):
        """Diferente de "/w" sem nome (erro, falta o alvo), "/p" sozinho
        é uma ação válida -- só liga o modo, sem precisar de alvo."""
        eng = self._make_engine()
        consumed = eng._try_handle_group_chat_command("/p")
        self.assertTrue(consumed)
        eng._net.send.assert_not_called()
        self.assertEqual(eng._chat_sticky_mode, ("party", None))
        self.assertEqual(len(eng._chat_local), 1)
        self.assertEqual(eng._chat_local[0][0], "Sistema")


class TestSayChatCommand(unittest.TestCase):
    """"/s" (03/10/2026, pedido do usuário, mesma ideia do WoW) -- sai
    do modo fixo (sussurro OU grupo), opcionalmente mandando uma
    mensagem no canal normal junto."""

    def _make_engine(self, chat_tab: str = "local", sticky=("whisper", "Fulano")):
        from client.chat_handlers import ChatHandlers
        from collections import deque

        class _FakeEngine(ChatHandlers):
            pass

        eng = _FakeEngine()
        eng._chat_local = deque(maxlen=500)
        eng._chat_world = deque(maxlen=500)
        eng._chat_tab = chat_tab
        eng._chat_sticky_mode = sticky
        eng._net = mock.Mock()
        return eng

    def test_texto_sem_barra_s_nao_e_consumido(self):
        eng = self._make_engine()
        self.assertFalse(eng._try_handle_say_chat_command("mensagem normal"))
        self.assertEqual(eng._chat_sticky_mode, ("whisper", "Fulano"),
                         "não deve alterar nada se não casou o comando")

    def test_s_sozinho_limpa_modo_fixo_e_nao_manda_nada(self):
        eng = self._make_engine()
        consumed = eng._try_handle_say_chat_command("/s")
        self.assertTrue(consumed)
        self.assertIsNone(eng._chat_sticky_mode)
        eng._net.send.assert_not_called()
        self.assertEqual(len(eng._chat_local), 1)
        self.assertEqual(eng._chat_local[0][0], "Sistema")

    def test_s_sai_do_modo_grupo_tambem(self):
        eng = self._make_engine(sticky=("party", None))
        eng._try_handle_say_chat_command("/s")
        self.assertIsNone(eng._chat_sticky_mode)

    def test_s_com_mensagem_limpa_modo_e_manda_no_canal_local(self):
        eng = self._make_engine(chat_tab="local")
        consumed = eng._try_handle_say_chat_command("/s oi pessoal")
        self.assertTrue(consumed)
        self.assertIsNone(eng._chat_sticky_mode)
        from shared.messages import MsgType
        eng._net.send.assert_called_once_with(MsgType.CHAT_SEND,
                                              {"text": "oi pessoal", "channel": "local"})

    def test_s_com_mensagem_na_aba_mundial_manda_pro_mundial(self):
        eng = self._make_engine(chat_tab="world")
        eng._try_handle_say_chat_command("/s oi mundo")
        from shared.messages import MsgType
        eng._net.send.assert_called_once_with(MsgType.CHAT_SEND,
                                              {"text": "oi mundo", "channel": "world"})


class TestStickyModeViaSendChatMessage(unittest.TestCase):
    """Integração via `_send_chat_message` de verdade -- confirma o
    pedido central do usuário: depois de "/w Nome msg" ou "/p msg",
    texto PLANO (sem comando) continua indo pro MESMO alvo/grupo, até
    outro "/w"/"/p" trocar ou "/s" sair."""

    def _make_engine(self):
        from client.chat_handlers import ChatHandlers
        from collections import deque

        class _FakeEngine(ChatHandlers):
            # Stubs dos outros comandos de chat (convite/arena/bg) --
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
        eng._chat_sticky_mode = None
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

        # "/s" sai do modo fixo -- texto plano seguinte volta pro local.
        self._send(eng, "/s")
        self.assertIsNone(eng._chat_sticky_mode)
        self._send(eng, "oi geral")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND, {"text": "oi geral", "channel": "local"})

    def test_fluxo_completo_p_depois_texto_plano_depois_w_troca_pro_whisper(self):
        from shared.messages import MsgType
        eng = self._make_engine()

        self._send(eng, "/p oi grupo")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND, {"text": "oi grupo", "channel": "party"})

        self._send(eng, "mais uma pro grupo")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND,
                                         {"text": "mais uma pro grupo", "channel": "party"})

        # "/w" troca de GRUPO pra SUSSURRO -- modo fixo é exclusivo.
        self._send(eng, "/w Fulano oi")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND,
                                         {"text": "oi", "channel": "whisper", "target_name": "Fulano"})
        self._send(eng, "mensagem privada")
        eng._net.send.assert_called_with(MsgType.CHAT_SEND, {
            "text": "mensagem privada", "channel": "whisper", "target_name": "Fulano",
        })

    def test_sem_nunca_ter_ativado_nada_texto_plano_vai_pro_canal_normal(self):
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
        self.assertEqual(reply_target, ("whisper", "Fulano"))
        self.assertEqual(color, (150, 210, 255))

    def test_eco_de_saida_mostra_para_fulano(self):
        """03/10/2026, pedido do usuário -- rótulo completo ("Você
        sussurra para Fulano") ficava grande demais no chat; só "para
        Fulano" (reply_target continua apontando pro mesmo nome)."""
        eng = self._make_engine()
        eng._handle_msg_chat_message({
            "sender": "EuMesmo", "eid": 1, "text": "oi",
            "channel": "whisper", "color": [150, 210, 255], "to": "Fulano",
        })
        label, text, color, reply_target = eng._chat_local[0]
        self.assertEqual(label, "para Fulano")
        self.assertEqual(reply_target, ("whisper", "Fulano"))

    def test_mensagem_normal_local_reply_target_e_o_sender(self):
        eng = self._make_engine()
        eng._handle_msg_chat_message({
            "sender": "Fulano", "eid": 5, "text": "oi", "channel": "local",
        })
        label, text, color, reply_target = eng._chat_local[0]
        self.assertEqual(label, "Fulano")
        self.assertEqual(reply_target, ("whisper", "Fulano"))

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

    def test_mensagem_de_grupo_mostra_grupo_barra_nome(self):
        """Formato exato pedido pelo usuário: "grupo/Fulano: mensagem"."""
        eng = self._make_engine()
        eng._handle_msg_chat_message({
            "sender": "Fulano", "eid": 5, "text": "vamos atacar",
            "channel": "party", "color": [140, 230, 150],
        })
        label, text, color, reply_target = eng._chat_local[0]
        self.assertEqual(label, "grupo/Fulano")
        self.assertEqual(reply_target, ("party", None))
        self.assertEqual(color, (140, 230, 150))


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

    def _click_on_name(self, eng, label: str, reply_target=None, msg: str = "mensagem de teste"):
        """Simula clique bem no início da área de mensagens -- a 1ª
        linha mostrada é sempre a MAIS RECENTE adicionada (scroll=0
        ancorado no fundo), então com 1 entrada só ela ocupa a última
        linha visível; clicamos perto do topo do texto dela usando a
        geometria real de `_chat_layout()`."""
        if reply_target is None:
            reply_target = ("whisper", label)
        eng._chat_local.append((label, msg, (255, 255, 255), reply_target))
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

    def test_clique_em_mensagem_de_grupo_abre_p_sem_nome(self):
        """03/10/2026, pedido do usuário -- clicar numa mensagem de
        grupo responde PRO GRUPO, nunca sussurra só pra quem mandou."""
        eng = self._make_engine()
        self._click_on_name(eng, "grupo/Fulano", reply_target=("party", None))
        self.assertTrue(eng._chat_active)
        self.assertEqual(eng._chat_text, "/p ")

    def test_clique_em_mensagem_de_grupo_do_proprio_nome_ainda_funciona(self):
        """Diferente do whisper, clicar na PRÓPRIA mensagem de grupo
        não é bloqueado -- reativar o modo de grupo é sempre válido."""
        eng = self._make_engine(own_name="EuMesmo")
        self._click_on_name(eng, "grupo/EuMesmo", reply_target=("party", None))
        self.assertTrue(eng._chat_active)
        self.assertEqual(eng._chat_text, "/p ")

    def test_clique_fora_da_regiao_do_nome_nao_faz_nada(self):
        eng = self._make_engine()
        eng._chat_local.append(("Fulano", "mensagem bem longa de teste",
                                (255, 255, 255), ("whisper", "Fulano")))
        L = eng._chat_layout()
        msg_area = L["msg_area"]
        # Clica no CANTO DIREITO da área de mensagens, longe de onde o
        # nome "Fulano:" é desenhado (que começa em msg_area.x).
        eng._try_handle_chat_name_click(msg_area.right - 2, msg_area.y + 2, msg_area)
        self.assertFalse(eng._chat_active)

    def test_clique_na_aba_combate_nunca_abre_resposta(self):
        eng = self._make_engine()
        eng._chat_tab = "combat"
        eng._chat_local.append(("Fulano", "oi", (255, 255, 255), ("whisper", "Fulano")))
        L = eng._chat_layout()
        msg_area = L["msg_area"]
        eng._try_handle_chat_name_click(msg_area.x + 2, msg_area.y + 2, msg_area)
        self.assertFalse(eng._chat_active)


if __name__ == "__main__":
    unittest.main(verbosity=2)
