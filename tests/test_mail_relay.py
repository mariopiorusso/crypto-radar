import asyncio
import os
import smtplib
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiosmtpd.controller import Controller
from crypto_radar.mail_relay import GmailRelay, settings


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.relay = GmailRelay('sender@gmail.com', 'test-only', 'sender@gmail.com', {'to@example.invalid'})
        self.envelope = SimpleNamespace(mail_from='', mail_options=[], rcpt_tos=[],
            original_content=b'From: sender@gmail.com\r\nTo: to@example.invalid\r\n\r\nTest\r\n')

    def test_sender_and_recipient_restrictions(self):
        for address, expected in [('sender@gmail.com', '250'), ('other@example.invalid', '550')]:
            self.assertTrue(asyncio.run(self.relay.handle_MAIL(None,None,self.envelope,address,[])).startswith(expected))
        for address, expected in [('to@example.invalid', '250'), ('other@example.invalid', '550')]:
            self.assertTrue(asyncio.run(self.relay.handle_RCPT(None,None,self.envelope,address,[])).startswith(expected))

    def test_forward_success_and_failure(self):
        with patch.object(self.relay,'forward') as forward:
            self.assertTrue(asyncio.run(self.relay.handle_DATA(None,None,self.envelope)).startswith('250'))
            forward.assert_called_once()
        with patch.object(self.relay,'forward',side_effect=TimeoutError):
            self.assertTrue(asyncio.run(self.relay.handle_DATA(None,None,self.envelope)).startswith('451'))

    def test_forged_header_is_rejected(self):
        self.envelope.original_content=b'From: other@example.invalid\r\n\r\nTest'
        with patch.object(self.relay,'forward') as forward:
            self.assertTrue(asyncio.run(self.relay.handle_DATA(None,None,self.envelope)).startswith('550'))
            forward.assert_not_called()

    def test_tls_and_login_are_mocked(self):
        with patch('crypto_radar.mail_relay.smtplib.SMTP') as smtp, patch('crypto_radar.mail_relay.ssl.create_default_context'):
            client=smtp.return_value.__enter__.return_value
            client.sendmail.return_value={}
            self.relay.forward(b'test',['to@example.invalid'])
            smtp.assert_called_once_with('smtp.gmail.com',587,timeout=10)
            client.starttls.assert_called_once()
            client.login.assert_called_once_with('sender@gmail.com','test-only')

    def test_missing_password_fails_before_startup(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'GMAIL_APP_PASSWORD'):
                settings()

    def test_loopback_controller_handshake_no_message(self):
        # Exercise Python 3.14 event-loop compatibility without sending any message.
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0))
            port=reservation.getsockname()[1]
        with patch.object(self.relay,'forward') as forward:
            controller=Controller(self.relay,hostname='127.0.0.1',port=port,ready_timeout=10)
            controller.start()
            try:
                with smtplib.SMTP('127.0.0.1',port,timeout=5) as client:
                    self.assertEqual(client.ehlo()[0],250)
            finally:
                controller.stop()
            forward.assert_not_called()
