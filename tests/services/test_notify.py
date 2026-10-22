"""tests/services/test_notify.py

เคสทดสอบของ app/services/notify.py

รายละเอียด:
 - LogNotifier log การส่งต่อ/การจบเคส มี conversation_id, เหตุผล, summary
 - ไม่ log close_url (มี token ใช้ครั้งเดียว) — log แค่ has_close_url
 - summary ผ่าน redact ซ้ำถ้ามี redact_text; redact พัง -> ไม่ปล่อยข้อความออก
 - log พัง -> ไม่ยก error กลับ (แจ้ง Sentry)
 - make_notifier: ช่องทางว่าง/log -> LogNotifier; ช่องทางอื่น (รอคำตอบ) -> LogNotifier + เตือน

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

import pytest

from app.pipeline.ports import Notifier
from app.pipeline.schemas import Conversation, ConversationStatus, HandoffEndReason
from app.services import notify as notify_mod
from app.services.notify import LogNotifier, make_notifier
from tests.fakes import DEFAULT_NOW, make_settings
from tests.services._helpers import RecordingLogger

CLOSE_URL = "https://bot.example.test/close?token=one-time-secret-token"


def conv() -> Conversation:
    return Conversation(
        id=7,
        user_id=3,
        status=ConversationStatus.HUMAN,
        opened_at=DEFAULT_NOW,
        handoff_started_at=DEFAULT_NOW,
        handoff_reason="rule:เคลม",
    )


@pytest.fixture
def logger(monkeypatch) -> RecordingLogger:
    rec = RecordingLogger()
    monkeypatch.setattr(notify_mod, "log", rec)
    return rec


@pytest.fixture
def no_redact(monkeypatch):
    monkeypatch.setattr(notify_mod, "_get_redact_text", lambda: None)


def test_implements_port():
    assert isinstance(LogNotifier(make_settings()), Notifier)


async def test_handoff_started_logs_without_close_url(logger, no_redact):
    await LogNotifier(make_settings(callcenter_notify_target="team-a")).handoff_started(
        conv(), "ลูกค้าขอเคลมสินค้า", CLOSE_URL
    )
    [(level, event, kw)] = logger.records
    assert event == "callcenter_handoff_started"
    assert kw["conversation_id"] == 7
    assert kw["handoff_reason"] == "rule:เคลม"
    assert kw["summary"] == "ลูกค้าขอเคลมสินค้า"
    assert kw["has_close_url"] is True
    assert kw["notify_target"] == "team-a"
    assert "one-time-secret-token" not in logger.dump()


async def test_handoff_started_without_close_url(logger, no_redact):
    await LogNotifier(make_settings()).handoff_started(conv(), "s", None)
    assert logger.records[0][2]["has_close_url"] is False


async def test_summary_goes_through_redact_when_available(logger, monkeypatch):
    monkeypatch.setattr(notify_mod, "_get_redact_text", lambda: lambda s: s.replace("0812345678", "[phone]"))
    await LogNotifier(make_settings()).handoff_started(conv(), "โทร 0812345678", None)
    assert logger.records[0][2]["summary"] == "โทร [phone]"
    assert "0812345678" not in logger.dump()


async def test_redact_failure_does_not_leak_text(logger, monkeypatch):
    def broken(s: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(notify_mod, "_get_redact_text", lambda: broken)
    await LogNotifier(make_settings()).handoff_started(conv(), "ข้อความลับ", None)
    assert "ข้อความลับ" not in logger.dump()


async def test_handoff_ended_logs(logger):
    await LogNotifier(make_settings()).handoff_ended(conv(), HandoffEndReason.USER_RESUMED)
    [(_, event, kw)] = logger.records
    assert event == "callcenter_handoff_ended"
    assert (kw["conversation_id"], kw["end_reason"]) == (7, "user_resumed")


class ExplodingOnceLogger(RecordingLogger):
    def warning(self, event, **kw):
        raise RuntimeError("log sink down")


async def test_failure_never_raises_and_reports(monkeypatch, no_redact):
    rec = ExplodingOnceLogger()
    monkeypatch.setattr(notify_mod, "log", rec)
    captured: list[BaseException] = []
    monkeypatch.setattr(notify_mod.sentry_sdk, "capture_exception", captured.append)
    n = LogNotifier(make_settings())
    await n.handoff_started(conv(), "s", CLOSE_URL)
    await n.handoff_ended(conv(), HandoffEndReason.TIMEOUT)
    assert len(captured) == 2
    assert rec.events() == ["callcenter_notify_failed", "callcenter_notify_failed"]


@pytest.mark.parametrize("channel", [None, "", "log", " LOG "])
def test_make_notifier_log_channels(logger, channel):
    n = make_notifier(make_settings(callcenter_notify_channel=channel))
    assert isinstance(n, LogNotifier)
    assert logger.records == []


def test_make_notifier_unknown_channel_falls_back_with_warning(logger):
    n = make_notifier(make_settings(callcenter_notify_channel="line_group"))
    assert isinstance(n, LogNotifier)
    assert logger.events() == ["callcenter_channel_not_implemented"]
