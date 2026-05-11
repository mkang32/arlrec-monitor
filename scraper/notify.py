"""Email alerts via Gmail SMTP. Credentials read from environment:
    ALERT_GMAIL_USER  — full gmail address (sender = recipient)
    ALERT_GMAIL_PASS  — Gmail App Password (16 chars, no spaces)
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def alerts_configured() -> bool:
    return bool(os.environ.get("ALERT_GMAIL_USER") and os.environ.get("ALERT_GMAIL_PASS"))


def send_alert(*, subject: str, body: str) -> None:
    user = os.environ["ALERT_GMAIL_USER"]
    pw = os.environ["ALERT_GMAIL_PASS"].replace(" ", "")  # App Passwords are shown with spaces
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = user
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=20) as s:
        s.login(user, pw)
        s.send_message(msg)
