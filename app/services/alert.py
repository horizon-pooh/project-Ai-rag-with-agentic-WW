"""app/services/alert.py

แจ้งเตือนผู้ดูแลระบบ (ไม่ใช่ลูกค้า ไม่ใช่ call center) — ใช้โดย watchdog (D-012)

รายละเอียด:
 - ช่องทางจาก ALERT_CHANNEL: ว่าง/log = log + Sentry; email = log + Sentry + อีเมลผ่าน SMTP
 - ไม่ใช้ LINE: ข้อความขาออกทาง LINE ต้องผ่าน sendGuard เท่านั้น และ LINE Notify ปิดบริการแล้ว (มี.ค. 2025)
 - ข้อความแจ้งเตือนมีแค่ชื่อปัญหา จำนวน และเวลา — ห้ามใส่ข้อมูลลูกค้า
 - ส่งไม่สำเร็จ -> log + Sentry แล้วคืน False ไม่ยก error (watchdog ต้องรันต่อ)
 - ตั้ง email แต่ค่า SMTP ไม่ครบ -> make_alerter ยก ValueError ตอนเริ่ม (fail fast ดีกว่าเงียบตอนระบบล่ม)

แนวคิดจาก: ใหม่ (D-012)
สถานะ: มีโค้ดแล้ว — รอคำตอบ: SMTP ของบริษัท / ผู้รับอีเมล
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Protocol

import sentry_sdk
import structlog

from app.config import Settings

log = structlog.get_logger(__name__)

LOG_CHANNELS = {"", "log"}
EMAIL_CHANNEL = "email"
SUBJECT_PREFIX = "[line_prod] "
SMTP_TIMEOUT_S = 20.0


class Alerter(Protocol):
    async def send(self, subject: str, body: str) -> bool:
        """ส่งแจ้งเตือนหนึ่งเรื่อง คืน True ถ้าทุกช่องทางสำเร็จ (ไม่ยก error)"""
        ...


def _capture(subject: str) -> None:
    try:
        sentry_sdk.capture_message(SUBJECT_PREFIX + subject, level="error")
    except Exception:
        pass


class LogAlerter:
    """log ระดับ error (ให้ระบบเก็บ log จับได้) + Sentry (ถ้าตั้ง SENTRY_DSN; ไม่ได้ตั้ง = ไม่ทำอะไร)"""

    async def send(self, subject: str, body: str) -> bool:
        try:
            log.error("ops_alert", subject=subject, body=body)
        except Exception:
            pass
        _capture(subject)
        return True


class EmailAlerter:
    """log + Sentry ก่อนเสมอ แล้วส่งอีเมล (smtplib ใน thread แยก ไม่บล็อก event loop)"""

    def __init__(self, settings: Settings) -> None:
        assert settings.smtp_host and settings.alert_email_from and settings.alert_email_to
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._user = settings.smtp_user
        self._password = (
            settings.smtp_password.get_secret_value() if settings.smtp_password else None
        )
        self._starttls = settings.smtp_starttls
        self._from = settings.alert_email_from
        self._to = parse_recipients(settings.alert_email_to)
        self._log_alerter = LogAlerter()

    def build_message(self, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = SUBJECT_PREFIX + subject
        msg["From"] = self._from
        msg["To"] = ", ".join(self._to)
        msg.set_content(body)
        return msg

    def _send_sync(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=SMTP_TIMEOUT_S) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._user:
                smtp.login(self._user, self._password or "")
            smtp.send_message(msg)

    async def send(self, subject: str, body: str) -> bool:
        await self._log_alerter.send(subject, body)
        try:
            await asyncio.to_thread(self._send_sync, self.build_message(subject, body))
        except Exception as e:
            try:
                log.error("ops_alert_email_failed", subject=subject, error_type=type(e).__name__)
                sentry_sdk.capture_exception(e)
            except Exception:
                pass
            return False
        log.info("ops_alert_email_sent", subject=subject, recipients=len(self._to))
        return True


def parse_recipients(value: str | None) -> list[str]:
    return [a.strip() for a in (value or "").split(",") if a.strip()]


def make_alerter(settings: Settings) -> Alerter:
    """เลือกตาม ALERT_CHANNEL — email ที่ตั้งไม่ครบ / ช่องทางที่ไม่รู้จัก = ValueError"""
    channel = (settings.alert_channel or "").strip().casefold()
    if channel in LOG_CHANNELS:
        return LogAlerter()
    if channel == EMAIL_CHANNEL:
        missing = [
            name
            for name, value in (
                ("SMTP_HOST", settings.smtp_host),
                ("ALERT_EMAIL_FROM", settings.alert_email_from),
                ("ALERT_EMAIL_TO", parse_recipients(settings.alert_email_to)),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"ALERT_CHANNEL=email แต่ไม่ได้ตั้ง {', '.join(missing)}")
        return EmailAlerter(settings)
    raise ValueError(f"ALERT_CHANNEL ไม่รู้จัก: {channel!r} (ใช้ได้: log, email)")


__all__ = ["Alerter", "EmailAlerter", "LogAlerter", "make_alerter", "parse_recipients"]
