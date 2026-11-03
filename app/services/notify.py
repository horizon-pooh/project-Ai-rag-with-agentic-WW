"""app/services/notify.py

แจ้ง call center เมื่อส่งต่อ / ลูกค้ากลับไปคุยกับ AI และเช็คเวลาทำการ

รายละเอียด:
 - ช่องทางจาก CALLCENTER_NOTIFY_CHANNEL / TARGET (ยังไม่ได้เลือกช่องทาง)
 - ข้อความแจ้งส่งต่อแนบลิงก์ /close ใช้ครั้งเดียว
 - เวลาทำการจาก CALLCENTER_HOURS: นอกเวลาบอกลูกค้าตามจริง
 - แจ้งไม่สำเร็จ -> log + Sentry (ส่งต่อแล้วต้องมีคนรู้)
 - ไม่ใส่ข้อมูลส่วนบุคคลเกินจำเป็นในข้อความแจ้ง

แนวคิดจาก: src/notify/callCenter.js, src/notify/businessHours.js (แบบเก่า)
สถานะ: มีโค้ดแล้วบางส่วน — LogNotifier (structlog อย่างเดียว) + make_notifier;
       รอคำตอบ: ช่องทางจริง (CALLCENTER_NOTIFY_CHANNEL); เช็คเวลาทำการยังไม่อยู่ในงาน 6
"""

from __future__ import annotations

from collections.abc import Callable

import sentry_sdk
import structlog

from app.config import Settings
from app.pipeline.ports import Notifier
from app.pipeline.schemas import Conversation, HandoffEndReason

log = structlog.get_logger(__name__)

# ช่องทางที่ทำแล้ว (ว่าง = log)
LOG_CHANNELS = {"", "log"}
_REDACT_FAILED = "[redact-failed]"


def _get_redact_text() -> Callable[[str], str] | None:
    # import ตอนเรียก: app/privacy/redact.py เขียนโดยงานอื่น (อาจยังไม่มี redact_text)
    try:
        from app.privacy.redact import redact_text
    except ImportError:
        return None
    return redact_text


def _redact(text: str) -> str:
    """summary ผู้เรียก redact มาแล้ว — redact ซ้ำอีกชั้นถ้ามีตัว redact; redact พัง -> ไม่ปล่อยข้อความออก"""
    redact = _get_redact_text()
    if redact is None:
        return text
    try:
        return redact(text)
    except Exception:
        return _REDACT_FAILED


class LogNotifier:
    """แจ้ง call center ทาง log (JSON ของ structlog) — ใช้จนกว่าจะเลือกช่องทางจริง (รอคำตอบ)

    - ไม่ log ข้อความดิบของลูกค้า: มีแค่ id, เหตุผล, summary (redact แล้ว)
    - close_url มี token ใช้ครั้งเดียว — ห้ามลง log (สัญญา ConversationsRepo.create_close_token)
      log แค่ has_close_url; ช่องทางจริงค่อยส่งลิงก์ให้พนักงาน
    - ไม่ยก error กลับ: พังแล้ว log + Sentry
    """

    def __init__(self, settings: Settings) -> None:
        self._target = settings.callcenter_notify_target

    async def handoff_started(self, conv: Conversation, summary: str, close_url: str | None) -> None:
        try:
            log.warning(
                "callcenter_handoff_started",
                conversation_id=conv.id,
                user_id=conv.user_id,
                handoff_reason=conv.handoff_reason,
                handoff_started_at=conv.handoff_started_at.isoformat() if conv.handoff_started_at else None,
                summary=_redact(summary),
                has_close_url=close_url is not None,
                notify_target=self._target,
            )
        except Exception as e:
            self._report_failure("handoff_started", conv, e)

    async def handoff_ended(self, conv: Conversation, reason: HandoffEndReason) -> None:
        try:
            log.warning(
                "callcenter_handoff_ended",
                conversation_id=conv.id,
                user_id=conv.user_id,
                end_reason=str(reason),
                notify_target=self._target,
            )
        except Exception as e:
            self._report_failure("handoff_ended", conv, e)

    @staticmethod
    def _report_failure(op: str, conv: Conversation, exc: Exception) -> None:
        try:
            log.error("callcenter_notify_failed", op=op, conversation_id=conv.id, error_type=type(exc).__name__)
            sentry_sdk.capture_exception(exc)
        except Exception:
            # แจ้งไม่ได้แม้แต่ log — ห้ามยก error กลับให้ pipeline
            pass


def make_notifier(settings: Settings) -> Notifier:
    """เลือก Notifier ตาม CALLCENTER_NOTIFY_CHANNEL — ตอนนี้มีแค่ log (รอคำตอบ: ช่องทางจริง)"""
    channel = (settings.callcenter_notify_channel or "").strip().casefold()
    if channel not in LOG_CHANNELS:
        log.warning(
            "callcenter_channel_not_implemented",
            channel=channel,
            fallback="log",
            note="รอคำตอบ: ช่องทางแจ้ง call center ยังไม่ได้ทำ ใช้ LogNotifier แทน",
        )
    return LogNotifier(settings)


__all__ = ["LogNotifier", "make_notifier"]
