"""
test_notifications.py — Tests for src/notifications.py.

All network I/O (requests.post, smtplib.SMTP) is mocked; these tests
never require real Telegram/email credentials or connectivity.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.notifications import AlertManager, EmailChannel, TelegramChannel


SAMPLE_SIGNAL = {
    "ticker": "RELIANCE.NS",
    "direction": "LONG",
    "kill_score": 8.2,
    "entry": 2500.0,
    "stop": 2450.0,
    "target": 2600.0,
    "shares": 40,
    "reasons": ["RS Sweet Spot", "Breadth Confirmed"],
}


def _clear_alert_env(monkeypatch):
    for var in [
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "ALERT_EMAIL_FROM", "ALERT_EMAIL_TO", "ALERT_EMAIL_APP_PASSWORD",
    ]:
        monkeypatch.delenv(var, raising=False)


class TestTelegramChannel:
    def test_disabled_without_env_vars(self, monkeypatch):
        _clear_alert_env(monkeypatch)
        ch = TelegramChannel()
        assert ch.enabled is False
        assert ch.send("hello") is False

    def test_sends_when_configured(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        ch = TelegramChannel()
        assert ch.enabled is True

        with patch("src.notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            assert ch.send("hello") is True
            args, kwargs = mock_post.call_args
            assert "abc123" in args[0]
            assert kwargs["data"]["chat_id"] == "999"
            assert kwargs["data"]["text"] == "hello"

    def test_network_failure_returns_false_not_raise(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        ch = TelegramChannel()

        with patch("src.notifications.requests.post", side_effect=ConnectionError("boom")):
            assert ch.send("hello") is False  # must not raise


class TestEmailChannel:
    def test_disabled_without_env_vars(self, monkeypatch):
        _clear_alert_env(monkeypatch)
        ch = EmailChannel()
        assert ch.enabled is False
        assert ch.send("subj", "body") is False

    def test_sends_when_configured(self, monkeypatch):
        monkeypatch.setenv("ALERT_EMAIL_FROM", "bot@example.com")
        monkeypatch.setenv("ALERT_EMAIL_TO", "me@example.com")
        monkeypatch.setenv("ALERT_EMAIL_APP_PASSWORD", "app-pass")
        ch = EmailChannel()
        assert ch.enabled is True

        mock_smtp_instance = MagicMock()
        mock_smtp_cm = MagicMock()
        mock_smtp_cm.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_cm.__exit__ = MagicMock(return_value=False)

        with patch("src.notifications.smtplib.SMTP", return_value=mock_smtp_cm) as mock_smtp:
            assert ch.send("Test Subject", "Test Body") is True
            mock_smtp_instance.starttls.assert_called_once()
            mock_smtp_instance.login.assert_called_once_with("bot@example.com", "app-pass")
            mock_smtp_instance.sendmail.assert_called_once()

    def test_smtp_failure_returns_false_not_raise(self, monkeypatch):
        monkeypatch.setenv("ALERT_EMAIL_FROM", "bot@example.com")
        monkeypatch.setenv("ALERT_EMAIL_TO", "me@example.com")
        monkeypatch.setenv("ALERT_EMAIL_APP_PASSWORD", "app-pass")
        ch = EmailChannel()

        with patch("src.notifications.smtplib.SMTP", side_effect=OSError("smtp down")):
            assert ch.send("subj", "body") is False  # must not raise


class TestAlertManager:
    def test_no_channels_configured_returns_empty_set_and_makes_no_calls(self, monkeypatch):
        _clear_alert_env(monkeypatch)
        manager = AlertManager()

        with patch("src.notifications.requests.post") as mock_post, \
             patch("src.notifications.smtplib.SMTP") as mock_smtp:
            sent = manager.send_signal_alert(SAMPLE_SIGNAL)
            assert sent == set()
            mock_post.assert_not_called()
            mock_smtp.assert_not_called()

    def test_sends_via_both_channels_when_configured(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        monkeypatch.setenv("ALERT_EMAIL_FROM", "bot@example.com")
        monkeypatch.setenv("ALERT_EMAIL_TO", "me@example.com")
        monkeypatch.setenv("ALERT_EMAIL_APP_PASSWORD", "app-pass")
        manager = AlertManager()

        mock_smtp_instance = MagicMock()
        mock_smtp_cm = MagicMock()
        mock_smtp_cm.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_cm.__exit__ = MagicMock(return_value=False)

        with patch("src.notifications.requests.post") as mock_post, \
             patch("src.notifications.smtplib.SMTP", return_value=mock_smtp_cm):
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            sent = manager.send_signal_alert(SAMPLE_SIGNAL)
            assert sent == {"telegram", "email"}

    def test_same_ticker_direction_not_re_alerted_same_day(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        manager = AlertManager()

        with patch("src.notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

            first = manager.send_signal_alert(SAMPLE_SIGNAL)
            second = manager.send_signal_alert(SAMPLE_SIGNAL)

            assert first == {"telegram"}
            assert second == set()  # deduped
            assert mock_post.call_count == 1

    def test_dedup_resets_on_new_day(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        manager = AlertManager()

        with patch("src.notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

            manager.send_signal_alert(SAMPLE_SIGNAL)
            assert mock_post.call_count == 1

            # Simulate the next trading day
            with patch("src.notifications.date") as mock_date:
                mock_date.today.return_value = date(2099, 1, 1)
                manager.send_signal_alert(SAMPLE_SIGNAL)
            assert mock_post.call_count == 2

    def test_failed_send_is_not_marked_as_alerted_and_can_retry(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        manager = AlertManager()

        with patch("src.notifications.requests.post", side_effect=ConnectionError("boom")):
            first = manager.send_signal_alert(SAMPLE_SIGNAL)
            assert first == set()

        # Channel recovers on the "next candle" — should retry, not be deduped
        with patch("src.notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            second = manager.send_signal_alert(SAMPLE_SIGNAL)
            assert second == {"telegram"}

    def test_force_bypasses_dedup(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        manager = AlertManager()

        with patch("src.notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            manager.send_signal_alert(SAMPLE_SIGNAL)
            sent = manager.send_signal_alert(SAMPLE_SIGNAL, force=True)
            assert sent == {"telegram"}
            assert mock_post.call_count == 2
