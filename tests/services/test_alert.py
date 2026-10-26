"""tests/services/test_alert.py

เคสทดสอบของ app/services/alert.py (ไม่ต่อ SMTP จริง — smtplib.SMTP เป็นของปลอม)

รายละเอียด:
 - LogAlerter: log error + Sentry capture_message
 - EmailAlerter: log + Sentry ก่อน แล้วส่งอีเมลผ่าน STARTTLS + login; SMTP พัง -> คืน False ไม่ยก error
 - make_alerter: ว่าง/log -> LogAlerter, email ครบ -> EmailAlerter, email ไม่ครบ / ช่องทางไม่รู้จัก -> ValueError

แนวคิดจาก: ใหม่ (D-012)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

import smtplib
from typing import Any

import pytest

from app.services import alert as alert_mod
from app.services.alert import EmailAlerter, LogAlerter, make_alerter, parse_recipients
from tests.fakes import make_settings
from tests.services._helpers import RecordingLogger

EMAIL_SETTINGS = {
    "alert_channel": "email",
    "smtp_host": "smtp.example.test",
    "smtp_port": 2525,
    "smtp_user": "bot",
    "smtp_password": "pw",
    "alert_email_from": "bot@example.test",
    "alert_email_to": "dev@example.test, lead@example.test",
}


@pytest.fixture
def logger(monkeypatch) -> RecordingLogger:
    rec = RecordingLogger()
    monkeypatch.setattr(alert_mod, "log", rec)
    return rec


@pytest.fixture
def sentry(monkeypatch) -> list[tuple[str, Any]]:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        alert_mod.sentry_sdk, "capture_message", lambda m, level=None: calls.append(("msg", m))
    )
    monkeypatch.setattr(alert_mod.sentry_sdk, "capture_exception", lambda e: calls.append(("exc", e)))
    return calls


class FakeSMTP:
    instances: list[FakeSMTP] = []
    fail_on: str | None = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.sent: list[Any] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.calls.append("quit")

    def _step(self, name: str) -> None:
        self.calls.append(name)
        if FakeSMTP.fail_on == name:
            raise smtplib.SMTPException(name)

    def starttls(self) -> None:
        self._step("starttls")

    def login(self, user: str, password: str) -> None:
        self._step("login")
        self.credentials = (user, password)

    def send_message(self, msg: Any) -> None:
        self._step("send")
        self.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.fail_on = None
    monkeypatch.setattr(alert_mod.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


async def test_log_alerter_logs_and_reports_to_sentry(logger, sentry):
    assert await LogAlerter().send("no_worker", "ไม่มี worker") is True
    assert logger.records == [("error", "ops_alert", {"subject": "no_worker", "body": "ไม่มี worker"})]
    assert sentry == [("msg", "[line_prod] no_worker")]


async def test_email_alerter_sends(logger, sentry, smtp):
    alerter = make_alerter(make_settings(**EMAIL_SETTINGS))
    assert isinstance(alerter, EmailAlerter)
    assert await alerter.send("no_worker", "ไม่มี worker") is True
    (conn,) = smtp.instances
    assert (conn.host, conn.port) == ("smtp.example.test", 2525)
    assert conn.calls == ["starttls", "login", "send", "quit"]
    assert conn.credentials == ("bot", "pw")
    (msg,) = conn.sent
    assert msg["Subject"] == "[line_prod] no_worker"
    assert msg["To"] == "dev@example.test, lead@example.test"
    assert "ไม่มี worker" in msg.get_content()
    # log + Sentry ก่อนส่งเมลเสมอ (เมลพังก็ยังมีร่องรอย)
    assert ("error", "ops_alert") == logger.records[0][:2]
    assert sentry == [("msg", "[line_prod] no_worker")]


async def test_email_alerter_without_starttls_or_login(smtp, logger, sentry):
    s = dict(EMAIL_SETTINGS, smtp_starttls=False)
    del s["smtp_user"], s["smtp_password"]
    await make_alerter(make_settings(**s)).send("x", "y")
    assert smtp.instances[0].calls == ["send", "quit"]


@pytest.mark.parametrize("step", ["starttls", "login", "send"])
async def test_email_failure_returns_false_and_reports(smtp, logger, sentry, step):
    smtp.fail_on = step
    alerter = make_alerter(make_settings(**EMAIL_SETTINGS))
    assert await alerter.send("no_worker", "body") is False
    assert any(e == "ops_alert_email_failed" for _, e, _ in logger.records)
    assert [k for k, _ in sentry] == ["msg", "exc"]


async def test_email_password_not_logged(smtp, logger, sentry):
    smtp.fail_on = "login"
    await make_alerter(make_settings(**EMAIL_SETTINGS)).send("x", "y")
    assert "pw" not in repr(logger.records)


@pytest.mark.parametrize("channel", [None, "", "log", " LOG "])
def test_make_alerter_log(channel):
    assert isinstance(make_alerter(make_settings(alert_channel=channel)), LogAlerter)


@pytest.mark.parametrize("missing", ["smtp_host", "alert_email_from", "alert_email_to"])
def test_make_alerter_email_incomplete(missing):
    s = dict(EMAIL_SETTINGS)
    del s[missing]
    with pytest.raises(ValueError, match=missing.upper()):
        make_alerter(make_settings(**s))


def test_make_alerter_email_blank_recipients():
    with pytest.raises(ValueError, match="ALERT_EMAIL_TO"):
        make_alerter(make_settings(**dict(EMAIL_SETTINGS, alert_email_to=" , ")))


def test_make_alerter_unknown_channel():
    with pytest.raises(ValueError, match="line"):
        make_alerter(make_settings(alert_channel="line"))


def test_parse_recipients():
    assert parse_recipients(" a@x , ,b@y ") == ["a@x", "b@y"]
    assert parse_recipients(None) == []
