"""
notifications.py — Optional Telegram / email alerting for live signals.

Both channels are opt-in and independent: whichever channel's required
env vars aren't set is silently disabled (logged once at startup)
rather than raising, so live_bot.py keeps running fine with either
channel, both, or neither configured. A network/API failure on a send
is caught and logged, never allowed to crash the caller's scan loop.

Env vars (see .env.example):
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    ALERT_EMAIL_FROM, ALERT_EMAIL_TO, ALERT_EMAIL_APP_PASSWORD,
    ALERT_EMAIL_SMTP_HOST (default smtp.gmail.com),
    ALERT_EMAIL_SMTP_PORT (default 587)

Quick self-test once your .env is filled in:
    python -m src.notifications
"""
import os
import smtplib
import ssl
from datetime import date
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

from src.logger import get_logger

load_dotenv()
log = get_logger(__name__)


def _format_signal_message(res: dict) -> str:
    reasons = ", ".join(res.get("reasons", []))
    return (
        f"{res['direction']} {res['ticker']} | Score {res['kill_score']}/10\n"
        f"Entry {res['entry']} | Stop {res['stop']} | Target {res['target']} | Qty {res['shares']}\n"
        f"Reasons: {reasons}"
    )


class TelegramChannel:
    API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            log.info("Telegram alerts disabled (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to enable).")

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                self.API_URL.format(token=self.token),
                data={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            log.error("Telegram alert failed: %s", e)
            return False


class EmailChannel:
    def __init__(self):
        self.from_addr = os.getenv("ALERT_EMAIL_FROM")
        self.to_addr = os.getenv("ALERT_EMAIL_TO")
        self.app_password = os.getenv("ALERT_EMAIL_APP_PASSWORD")
        self.smtp_host = os.getenv("ALERT_EMAIL_SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("ALERT_EMAIL_SMTP_PORT", "587"))
        self.enabled = bool(self.from_addr and self.to_addr and self.app_password)
        if not self.enabled:
            log.info("Email alerts disabled (set ALERT_EMAIL_FROM/TO/APP_PASSWORD to enable).")

    def send(self, subject: str, body: str) -> bool:
        if not self.enabled:
            return False
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = self.from_addr
            msg["To"] = self.to_addr

            context = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.starttls(context=context)
                server.login(self.from_addr, self.app_password)
                server.sendmail(self.from_addr, [self.to_addr], msg.as_string())
            return True
        except Exception as e:
            log.error("Email alert failed: %s", e)
            return False


class AlertManager:
    """
    Fan-out alert dispatcher for live signals.

    Dedupes by (ticker, direction) within a calendar day so a setup
    that's still valid on the next 5-min candle doesn't re-fire an
    alert every single scan — without this, a persistent setup during
    live_bot.py's polling loop would spam a message every 5 minutes.
    A ticker is only marked as "alerted" once at least one channel
    actually succeeds, so a transient send failure gets retried on the
    next candle rather than silently dropped.
    """

    def __init__(self):
        self.telegram = TelegramChannel()
        self.email = EmailChannel()
        self._alerted_today = set()
        self._current_day = None

    def _reset_if_new_day(self):
        today = date.today()
        if self._current_day != today:
            self._current_day = today
            self._alerted_today = set()

    def send_signal_alert(self, res: dict, force: bool = False) -> set:
        """
        Sends `res` (a scanner result dict — same shape as a
        Scanner.scan_market() row) to every configured channel.

        Returns the set of channel names ({"telegram", "email"}) that
        actually sent successfully. Empty set means either nothing is
        configured, or this (ticker, direction) was already alerted
        today and `force` wasn't set.
        """
        self._reset_if_new_day()

        if not self.telegram.enabled and not self.email.enabled:
            return set()

        dedup_key = (res["ticker"], res["direction"])
        if not force and dedup_key in self._alerted_today:
            return set()

        message = _format_signal_message(res)
        sent = set()

        if self.telegram.send(f"\U0001F514 SIGNAL\n{message}"):
            sent.add("telegram")

        if self.email.send(f"[Trading Signal] {res['direction']} {res['ticker']}", message):
            sent.add("email")

        if sent:
            self._alerted_today.add(dedup_key)

        return sent


if __name__ == "__main__":
    manager = AlertManager()
    dummy_signal = {
        "ticker": "TEST.NS",
        "direction": "LONG",
        "kill_score": 8.5,
        "entry": 100.0,
        "stop": 97.0,
        "target": 106.0,
        "shares": 100,
        "reasons": ["Test Alert - Configuration Check"],
    }
    sent_via = manager.send_signal_alert(dummy_signal, force=True)
    if sent_via:
        print(f"\u2705 Test alert sent via: {', '.join(sorted(sent_via))}")
    else:
        print("\u26a0\ufe0f  No channels configured (or all sends failed). Check your .env file.")
