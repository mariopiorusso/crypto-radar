"""SMTP settings and credentials are read exclusively from environment variables."""
import os
import smtplib
import ssl
from ipaddress import ip_address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


def configured():
    return all(os.getenv(k, "").strip() for k in ("SMTP_HOST", "EMAIL_FROM", "EMAIL_TO"))


def send_alert(text, *, subject="Crypto Radar experimental signal", attachments=()):
    if not configured():
        raise ValueError("Email configuration missing")
    security = os.getenv("SMTP_SECURITY", "starttls").lower()
    if security not in ("starttls", "ssl", "local"):
        raise ValueError("SMTP_SECURITY must be starttls, ssl or local")
    host = os.environ["SMTP_HOST"].strip()
    if security == "local":
        # Require a literal loopback address: no DNS resolution or remote plaintext.
        if not ip_address(host).is_loopback:
            raise ValueError("Local SMTP requires a loopback IP address")
        if os.getenv("SMTP_USERNAME") or os.getenv("SMTP_PASSWORD"):
            raise ValueError("Local SMTP must not send credentials")
    message = EmailMessage()
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    message["From"] = os.environ["EMAIL_FROM"]
    recipients = [r.strip() for r in os.environ["EMAIL_TO"].split(",") if r.strip()]
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    for filename, content in attachments:
        subtype = "zip" if filename.lower().endswith(".zip") else "octet-stream"
        message.add_attachment(content, maintype="application", subtype=subtype, filename=filename)
    context = ssl.create_default_context() if security != "local" else None
    port = int(os.getenv("SMTP_PORT", "465" if security == "ssl" else "587"))
    factory = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
    kwargs = {"context": context} if security == "ssl" else {}
    with factory(host, port, timeout=180 if security == "local" else 60, **kwargs) as smtp:
        if security == "starttls":
            smtp.starttls(context=context)
        username = os.getenv("SMTP_USERNAME")
        if username:
            smtp.login(username, os.environ["SMTP_PASSWORD"])
        refused = smtp.send_message(message, to_addrs=recipients)
        if refused:
            # Some recipients may have received the email. Never blindly resend.
            raise RuntimeError("Partial email delivery")
    return None
