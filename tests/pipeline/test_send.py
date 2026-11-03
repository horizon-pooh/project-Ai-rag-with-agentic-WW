"""tests/pipeline/test_send.py — sendGuard (app/pipeline/steps/send.py)"""

from __future__ import annotations

import uuid

import pytest
from structlog.testing import capture_logs

from app.pipeline import replies
from app.pipeline.ports import Deps
from app.pipeline.schemas import LineApiError, OutboundStatus, SendStatus
from app.pipeline.steps.send import push_retry_key, send
from tests.fakes import FakeKillSwitch, make_deps

USER = "U-test"
OUT = replies.outbound_text("ai_answer", "สวัสดีค่ะ")


async def _setup(**deps_kw) -> tuple[Deps, int]:
    deps = make_deps(**deps_kw)
    user = await deps.users.get_or_create(USER)
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    return deps, conv.id


async def _send(
    deps: Deps, conv_id: int, *, raw_event_id: int | None = 1, key: str = "out:1:ai_answer", **kw
):
    return await send(
        deps,
        conversation_id=conv_id,
        line_user_id=USER,
        raw_event_id=raw_event_id,
        outbound=OUT,
        dedupe_key=key,
        **kw,
    )


def _rows(deps: Deps):
    return deps.messages.outbound_rows()  # type: ignore[attr-defined]


async def test_reply_when_token_exists() -> None:
    deps, cid = await _setup()
    await deps.reply_tokens.put(1, "rt-1")
    r = await _send(deps, cid, text_redacted="สวัสดีค่ะ")
    assert r.status is SendStatus.SENT and r.via == "reply"
    assert deps.line.replies == [("rt-1", OUT.messages)]  # type: ignore[attr-defined]
    assert deps.line.pushes == []  # type: ignore[attr-defined]
    (row,) = _rows(deps)
    assert row.id == r.message_id
    assert row.status is OutboundStatus.SENT and row.sent_at is not None
    assert row.message_type == "ai_answer" and row.text_redacted == "สวัสดีค่ะ"


async def test_push_when_no_token_with_stable_retry_key() -> None:
    deps, cid = await _setup()
    r = await _send(deps, cid)
    assert r.status is SendStatus.SENT and r.via == "push"
    ((user, msgs, retry_key),) = deps.line.pushes  # type: ignore[attr-defined]
    assert user == USER and msgs == OUT.messages
    assert retry_key == push_retry_key("out:1:ai_answer")
    assert uuid.UUID(retry_key).version == 5
    assert push_retry_key("out:1:ai_answer") == retry_key  # คงที่
    assert push_retry_key("out:2:ai_answer") != retry_key


async def test_push_when_token_expired_or_no_event() -> None:
    deps, cid = await _setup()
    await deps.reply_tokens.put(1, "rt-1")
    deps.clock.advance(seconds=deps.settings.reply_token_ttl_s + 1)  # type: ignore[attr-defined]
    assert (await _send(deps, cid)).via == "push"
    r = await _send(deps, cid, raw_event_id=None, key="out:cron:1")
    assert r.status is SendStatus.SENT and r.via == "push"


async def test_kill_switch_drops_without_line_call() -> None:
    deps, cid = await _setup(kill_switch=FakeKillSwitch(on=True))
    await deps.reply_tokens.put(1, "rt-1")
    with capture_logs() as logs:
        r = await _send(deps, cid)
    assert r.status is SendStatus.DROPPED_KILL_SWITCH
    assert deps.line.sent == []  # type: ignore[attr-defined]
    (row,) = _rows(deps)
    assert row.status is OutboundStatus.DROPPED and row.drop_reason == "kill_switch"
    assert any(
        e["event"] == "send.dropped" and e["decision"] == "dropped_kill_switch" for e in logs
    )
    assert await deps.reply_tokens.take(1) == "rt-1"  # ไม่ได้ใช้ token


async def test_kill_switch_drops_even_with_allow_when_human() -> None:
    deps, cid = await _setup(kill_switch=FakeKillSwitch(on=True))
    r = await _send(deps, cid, allow_when_human=True)
    assert r.status is SendStatus.DROPPED_KILL_SWITCH


async def test_human_conversation_drops_ai_answer() -> None:
    # escalationRace: ลูกค้าขอพนักงานระหว่างรอ AI แล้วคำตอบ AI มาถึงทีหลัง
    deps, cid = await _setup()
    await deps.reply_tokens.put(1, "rt-1")
    await deps.conversations.start_handoff(cid, "fast_path")
    with capture_logs() as logs:
        r = await _send(deps, cid)
    assert r.status is SendStatus.DROPPED_HUMAN
    assert deps.line.sent == []  # type: ignore[attr-defined]
    (row,) = _rows(deps)
    assert row.status is OutboundStatus.DROPPED and row.drop_reason == "conversation_human"
    assert any(e["event"] == "send.dropped" and e["decision"] == "dropped_human" for e in logs)


async def test_allow_when_human_sends_handoff_notice() -> None:
    deps, cid = await _setup()
    await deps.conversations.start_handoff(cid, "rule")
    out = replies.outbound_text("handoff", replies.HANDOFF_IN_HOURS)
    r = await send(
        deps,
        conversation_id=cid,
        line_user_id=USER,
        raw_event_id=1,
        outbound=out,
        dedupe_key="out:1:handoff",
        allow_when_human=True,
    )
    assert r.status is SendStatus.SENT


async def test_duplicate_not_resent() -> None:
    deps, cid = await _setup()
    first = await _send(deps, cid)
    with capture_logs() as logs:
        again = await _send(deps, cid)
    assert again.status is SendStatus.DUPLICATE and again.message_id == first.message_id
    assert len(deps.line.pushes) == 1  # type: ignore[attr-defined]
    assert any(e["event"] == "send.duplicate" for e in logs)


async def test_duplicate_wins_over_drop_and_keeps_sent_row() -> None:
    deps, cid = await _setup()
    await _send(deps, cid)
    await deps.conversations.start_handoff(cid, "x")
    r = await _send(deps, cid)
    assert r.status is SendStatus.DUPLICATE
    (row,) = _rows(deps)
    assert row.status is OutboundStatus.SENT


async def test_dropped_row_can_be_sent_on_retry() -> None:
    ks = FakeKillSwitch(on=True)
    deps, cid = await _setup(kill_switch=ks)
    assert (await _send(deps, cid)).status is SendStatus.DROPPED_KILL_SWITCH
    ks.on = False
    r = await _send(deps, cid)
    assert r.status is SendStatus.SENT
    (row,) = _rows(deps)
    assert row.status is OutboundStatus.SENT


@pytest.mark.parametrize("status_code", [400, 410])
async def test_invalid_reply_token_falls_back_to_push(status_code: int) -> None:
    deps, cid = await _setup()
    await deps.reply_tokens.put(1, "rt-bad")
    deps.line.reply_errors.append(  # type: ignore[attr-defined]
        LineApiError("Invalid reply token", status_code=status_code, retryable=False)
    )
    r = await _send(deps, cid)
    assert r.status is SendStatus.SENT and r.via == "push"
    assert len(deps.line.pushes) == 1  # type: ignore[attr-defined]


async def test_retryable_reply_error_fails_without_push() -> None:
    deps, cid = await _setup()
    await deps.reply_tokens.put(1, "rt-1")
    deps.line.reply_errors.append(LineApiError("5xx", status_code=503, retryable=True))  # type: ignore[attr-defined]
    r = await _send(deps, cid)
    assert r.status is SendStatus.FAILED
    assert deps.line.pushes == []  # type: ignore[attr-defined]
    (row,) = _rows(deps)
    assert row.status is OutboundStatus.FAILED and row.drop_reason == "line_error:503"


async def test_push_after_bad_token_fails_marks_failed() -> None:
    deps, cid = await _setup()
    await deps.reply_tokens.put(1, "rt-bad")
    deps.line.reply_errors.append(LineApiError("bad token", status_code=400, retryable=False))  # type: ignore[attr-defined]
    deps.line.push_errors.append(LineApiError("down", status_code=None, retryable=True))  # type: ignore[attr-defined]
    r = await _send(deps, cid)
    assert r.status is SendStatus.FAILED
    (row,) = _rows(deps)
    assert row.status is OutboundStatus.FAILED and row.drop_reason == "line_error:network"


async def test_push_failure_marks_failed_then_retry_sends() -> None:
    deps, cid = await _setup()
    deps.line.push_errors.append(LineApiError("429", status_code=429, retryable=True))  # type: ignore[attr-defined]
    with capture_logs() as logs:
        r = await _send(deps, cid)
    assert r.status is SendStatus.FAILED
    assert any(e["event"] == "send.failed" for e in logs)
    (row,) = _rows(deps)
    assert row.status is OutboundStatus.FAILED and row.drop_reason == "line_error:429"
    # retry ของคิว: แถว failed ส่งใหม่ได้ด้วย retry key เดิม
    r2 = await _send(deps, cid)
    assert r2.status is SendStatus.SENT and r2.message_id == r.message_id


async def test_push_409_retry_key_already_accepted_counts_as_sent() -> None:
    deps, cid = await _setup()
    deps.line.push_errors.append(LineApiError("conflict", status_code=409, retryable=False))  # type: ignore[attr-defined]
    r = await _send(deps, cid)
    assert r.status is SendStatus.SENT and r.via == "push"
    (row,) = _rows(deps)
    assert row.status is OutboundStatus.SENT


async def test_missing_conversation_is_not_sent() -> None:
    deps, _ = await _setup()
    r = await _send(deps, 999)
    assert r.status is SendStatus.FAILED
    assert deps.line.sent == []  # type: ignore[attr-defined]
    assert _rows(deps) == []
