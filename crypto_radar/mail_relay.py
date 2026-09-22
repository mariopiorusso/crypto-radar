"""Loopback-only SMTP forwarding to Gmail; credentials stay in the environment."""
import argparse
import asyncio
import logging
import os
import smtplib
import ssl
import threading
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from aiosmtpd.controller import Controller
from dotenv import load_dotenv

from .config import ROOT
from .operational import setup_logging

log = logging.getLogger(__name__)


def settings():
    username = os.getenv("GMAIL_USERNAME", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    sender = os.getenv("EMAIL_FROM", "").strip()
    recipients = {r.strip().lower() for r in os.getenv("EMAIL_TO", "").split(",") if r.strip()}
    if not username or not password:
        raise ValueError("Set GMAIL_USERNAME and GMAIL_APP_PASSWORD in .env before starting the relay")
    if sender.lower() != username.lower():
        raise ValueError("EMAIL_FROM must match GMAIL_USERNAME for this Gmail relay")
    if not recipients or any("@" not in r or "\n" in r or "\r" in r for r in recipients):
        raise ValueError("EMAIL_TO must contain valid recipient addresses")
    port = int(os.getenv("SMTP_PORT", "1025"))
    if not 1024 <= port <= 65535:
        raise ValueError("Local relay port must be 1024..65535")
    if os.getenv("SMTP_HOST") != "127.0.0.1" or os.getenv("SMTP_SECURITY") != "local":
        raise ValueError("Use SMTP_HOST=127.0.0.1 and SMTP_SECURITY=local")
    return username, password, sender, recipients, port


class GmailRelay:
    def __init__(self, username, password, sender, recipients):
        self.username = username
        self.password = password
        self.sender = sender
        self.recipients = recipients
        self._busy = asyncio.Lock()

    async def handle_MAIL(self, server, session, envelope, address, mail_options):
        if address.lower() != self.sender.lower():
            return "550 Sender not permitted"
        envelope.mail_from = address
        envelope.mail_options.extend(mail_options)
        return "250 OK"

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if address.lower() not in self.recipients:
            return "550 Recipient not permitted"
        if address.lower() not in {r.lower() for r in envelope.rcpt_tos}:
            envelope.rcpt_tos.append(address)
        return "250 OK"

    def forward(self, data, recipients):
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(self.username, self.password)
            refused = smtp.sendmail(self.sender, recipients, data)
            if refused:
                raise RuntimeError("Partial delivery")

    async def handle_DATA(self, server, session, envelope):
        message = BytesParser(policy=policy.default).parsebytes(envelope.original_content)
        headers = message.get_all("From", [])
        if len(headers) != 1 or parseaddr(str(headers[0]))[1].lower() != self.sender.lower():
            return "550 From header not permitted"
        if self._busy.locked():
            return "451 Relay busy; message not accepted"
        async with self._busy:
            try:
                await asyncio.to_thread(self.forward, envelope.original_content, envelope.rcpt_tos)
            except Exception as exc:
                # Never log exception bodies, credentials, or message contents.
                log.error("Gmail forwarding failed: %s", type(exc).__name__)
                return "451 Gmail forwarding failed; delivery may be uncertain"
        log.info("Gmail accepted alert for forwarding")
        return "250 Accepted by Gmail"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate settings without network access")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    try:
        username, password, sender, recipients, port = settings()
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    if args.check:
        print("Gmail relay configuration is present; credentials have not been authenticated.")
        return
    setup_logging({"path": str(ROOT / "logs/mail-relay.log"), "max_bytes": 1000000, "backup_count": 3})
    logging.getLogger("mail.log").setLevel(logging.CRITICAL)
    controller = Controller(GmailRelay(username, password, sender, recipients),
        hostname="127.0.0.1", port=port, enable_SMTPUTF8=False,
        data_size_limit=25000000, ready_timeout=10)
    controller.start()
    log.info("Local Gmail relay listening on 127.0.0.1:%s", port)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
