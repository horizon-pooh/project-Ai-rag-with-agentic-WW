"""tests/pipeline/test_schemas.py — การแปลง event ของ LINE และ invariant ของ schemas / replies / logging"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.logging import REDACTED, scrub_processor
from app.pipeline import replies
from app.pipeline.schemas import (
    AI_RAW_MAX_CHARS,
    AiKind,
    AiResult,
    EventType,
    InboundEvent,
    OutboundMessage,
    PostbackAction,
    ValidationResult,
    parse_postback_data,
    postback_data,
)


def test_from_line_event_text() -> None:
    ev = InboundEvent.from_line_event(
        5,
        {
            "type": "message",
            "webhookEventId": "wh",
            "timestamp": 1_700_000_000_000,
            "source": {"type": "user", "userId": "U1"},
            "message": {"type": "text", "id": "m1", "text": "ตู้เย็น"},
        },
    )
    assert ev.event_type is EventType.MESSAGE and ev.text == "ตู้เย็น" and ev.line_message_id == "m1"
    assert ev.occurred_at == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_from_line_event_postback_and_unknown() -> None:
    base = {"webhookEventId": "wh", "timestamp": 1, "source": {"userId": "U1"}}
    ev = InboundEvent.from_line_event(1, {**base, "type": "postback", "postback": {"data": "action=detail&sku=A%201"}})
    assert ev.postback_action == "detail" and ev.postback_params == {"action": "detail", "sku": "A 1"}
    assert InboundEvent.from_line_event(1, {**base, "type": "beacon"}).event_type is EventType.OTHER
    with pytest.raises(ValueError):
        InboundEvent.from_line_event(1, {**base, "type": "message", "source": {"type": "group"}})


def test_postback_roundtrip() -> None:
    data = postback_data(PostbackAction.FEEDBACK, v="up", run=12)
    assert parse_postback_data(data) == {"action": "feedback", "v": "up", "run": "12"}


def test_ai_result_raw_truncated() -> None:
    r = AiResult(kind=AiKind.ANSWER, raw="x" * (AI_RAW_MAX_CHARS + 100))
    assert len(r.raw) < AI_RAW_MAX_CHARS + 20


def test_outbound_and_validation_invariants() -> None:
    with pytest.raises(ValidationError):
        OutboundMessage(messages=[], purpose="x")
    with pytest.raises(ValidationError):
        OutboundMessage(messages=[{"type": "text", "text": "a"}] * 6, purpose="x")
    with pytest.raises(ValidationError):
        ValidationResult(ok=True)
    with pytest.raises(ValidationError):
        ValidationResult(ok=False)


def test_replies_quick_reply() -> None:
    msg = replies.state_expired_message()
    assert msg["text"] == replies.STATE_EXPIRED
    (item,) = msg["quickReply"]["items"]
    assert item["action"]["data"] == "action=restart" and item["action"]["label"] == "เริ่มใหม่"
    assert replies.ai_down_message()["quickReply"]["items"][0]["action"]["data"] == "action=talk_to_staff"
    assert "09:00-18:00" in replies.handoff_text(in_hours=False, hours="09:00-18:00")
    assert replies.handoff_text(in_hours=True, hours="09:00-18:00") == replies.HANDOFF_IN_HOURS


def test_log_scrub_drops_secret_keys() -> None:
    out = scrub_processor(
        None,
        "info",
        {
            "event": "sent",
            "reply_token": "a",
            "replyToken": "b",
            "access_token": "c",
            "channel_secret": "d",
            "api_key": "e",
            "nested": {"Authorization_key": "f", "ok": 1},
            "level": "info",
        },
    )
    assert all(out[k] == REDACTED for k in ("reply_token", "replyToken", "access_token", "channel_secret", "api_key"))
    assert out["nested"] == {"Authorization_key": REDACTED, "ok": 1}
    assert out["level"] == "info"
