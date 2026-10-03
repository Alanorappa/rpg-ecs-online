"""
tests/test_notify.py — server/notify.py (02/10/2026, pedido do
usuário: e-mail quando criam conta, criam personagem, ou logam).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import unittest
from unittest import mock

from server import notify


_FULL_ENV = {
    "RPG_SMTP_HOST": "smtp.gmail.com",
    "RPG_SMTP_PORT": "587",
    "RPG_SMTP_USER": "sender@gmail.com",
    "RPG_SMTP_PASS": "app-password",
    "RPG_NOTIFY_EMAIL_TO": "dest@gmail.com",
}


class TestSmtpConfig(unittest.TestCase):

    def test_sem_env_vars_fica_vazio(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = notify._smtp_config()
        self.assertEqual(cfg["host"], "")
        self.assertFalse(notify._configured(cfg))

    def test_com_todas_as_env_vars_fica_configurado(self):
        with mock.patch.dict(os.environ, _FULL_ENV, clear=False):
            cfg = notify._smtp_config()
        self.assertTrue(notify._configured(cfg))
        self.assertEqual(cfg["user"], "sender@gmail.com")
        self.assertEqual(cfg["to"], "dest@gmail.com")

    def test_faltando_uma_env_var_fica_desconfigurado(self):
        """Qualquer uma das 4 faltando já é suficiente pra não disparar
        -- nunca manda e-mail incompleto/quebrado."""
        for missing in ("RPG_SMTP_HOST", "RPG_SMTP_USER", "RPG_SMTP_PASS", "RPG_NOTIFY_EMAIL_TO"):
            env = dict(_FULL_ENV)
            env[missing] = ""
            with mock.patch.dict(os.environ, env, clear=False):
                cfg = notify._smtp_config()
            self.assertFalse(notify._configured(cfg), f"faltando {missing} deveria ficar desconfigurado")


class TestSendEmailSync(unittest.TestCase):
    """`_send_email_sync` é a chamada bloqueante real -- smtplib.SMTP
    mockado, nunca manda e-mail de verdade em teste."""

    def test_monta_e_envia_via_smtp_real(self):
        with mock.patch.dict(os.environ, _FULL_ENV, clear=False):
            cfg = notify._smtp_config()
        fake_smtp = mock.MagicMock()
        fake_smtp.__enter__.return_value = fake_smtp
        with mock.patch("smtplib.SMTP", return_value=fake_smtp) as m_smtp:
            notify._send_email_sync(cfg, "Assunto", "Corpo do teste")

        m_smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
        fake_smtp.starttls.assert_called_once()
        fake_smtp.login.assert_called_once_with("sender@gmail.com", "app-password")
        self.assertEqual(fake_smtp.sendmail.call_count, 1)
        args = fake_smtp.sendmail.call_args[0]
        self.assertEqual(args[0], "sender@gmail.com")
        self.assertEqual(args[1], ["dest@gmail.com"])
        self.assertIn("Assunto", args[2])
        import email
        parsed = email.message_from_string(args[2])
        self.assertEqual(parsed.get_payload(decode=True).decode("utf-8"), "Corpo do teste")


class TestNotifyAsync(unittest.IsolatedAsyncioTestCase):
    """`notify_async` -- no-op sem configuração; dispara task em
    background quando configurado; falha de envio nunca propaga pro
    chamador."""

    async def test_sem_configuracao_nao_cria_task(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(notify, "_send_email_sync") as m_send:
                notify.notify_async("assunto", "corpo")
                await asyncio.sleep(0)
        m_send.assert_not_called()

    async def test_configurado_dispara_envio_em_background(self):
        with mock.patch.dict(os.environ, _FULL_ENV, clear=False):
            with mock.patch.object(notify, "_send_email_sync") as m_send:
                notify.notify_async("assunto", "corpo")
                # notify_async nunca é awaited -- a task roda sozinha no
                # loop; aguardar ela de verdade (em vez de sleep(0) às
                # cegas) é o jeito determinístico de confirmar que
                # executou antes de checar as chamadas.
                task = next(iter(notify._pending_tasks))
                await asyncio.wait_for(task, timeout=2.0)
        self.assertEqual(m_send.call_count, 1)
        cfg_arg, subject_arg, body_arg = m_send.call_args[0]
        self.assertEqual(subject_arg, "assunto")
        self.assertEqual(body_arg, "corpo")

    async def test_falha_no_envio_nunca_propaga(self):
        """Mesmo com o SMTP explodindo, notify_async() em si nunca
        levanta -- e a task em background também não deixa a exceção
        "não retirada" (logada, não propagada)."""
        with mock.patch.dict(os.environ, _FULL_ENV, clear=False):
            with mock.patch.object(notify, "_send_email_sync",
                                   side_effect=RuntimeError("SMTP fora do ar")):
                try:
                    notify.notify_async("assunto", "corpo")
                except Exception:
                    self.fail("notify_async não pode levantar, mesmo com SMTP falhando")
                task = next(iter(notify._pending_tasks))
                await asyncio.wait_for(task, timeout=2.0)
        # Task terminou sem exception "não retirada" (log.error já
        # consumiu via try/except dentro de _run).
        self.assertEqual(len(notify._pending_tasks), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
