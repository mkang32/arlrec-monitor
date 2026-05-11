"""Alerting via Gmail SMTP and optional Slack/Discord webhook.

Environment variables read by these helpers:
    ALERT_GMAIL_USER     — full gmail address (sender = recipient)
    ALERT_GMAIL_PASS     — Gmail App Password (16 chars; spaces tolerated)
    ALERT_WEBHOOK_URL    — Slack or Discord incoming-webhook URL (optional)

`send_alert` fires both channels independently; failures in one don't block the other.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

log = logging.getLogger(__name__)


def email_configured() -> bool:
    return bool(os.environ.get("ALERT_GMAIL_USER") and os.environ.get("ALERT_GMAIL_PASS"))


def webhook_configured() -> bool:
    return bool(os.environ.get("ALERT_WEBHOOK_URL"))


def alerts_configured() -> bool:
    return email_configured() or webhook_configured()


def send_alert(*, subject: str, body: str) -> None:
    """Send alert to all configured channels. Each channel's failure is logged
    but does not propagate; a webhook outage shouldn't prevent an email."""
    if email_configured():
        try:
            _send_email(subject=subject, body=body)
        except Exception:
            log.exception("email alert failed")
    if webhook_configured():
        try:
            _send_webhook(subject=subject, body=body)
        except Exception:
            log.exception("webhook alert failed")


def _send_email(*, subject: str, body: str) -> None:
    user = os.environ["ALERT_GMAIL_USER"]
    pw = os.environ["ALERT_GMAIL_PASS"].replace(" ", "")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = user
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=20) as s:
        s.login(user, pw)
        s.send_message(msg)


def _send_webhook(*, subject: str, body: str) -> None:
    url = os.environ["ALERT_WEBHOOK_URL"]
    text = f"*{subject}*\n```\n{body}\n```"
    # Slack uses "text", Discord uses "content". Posting both is harmless — each
    # endpoint ignores the field it doesn't recognize, so the same payload works
    # for both providers and we don't need URL sniffing.
    payload = json.dumps({"text": text, "content": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"webhook HTTP {resp.status}: {resp.read()[:200]!r}")
