"""tests/repositories/test_pg_repos.py — repository ที่เหลือบน Postgres จริง + การลบตาม PDPA

ข้ามทั้งไฟล์ถ้าไม่ได้ตั้ง TEST_DATABASE_URL (fixture db_conninfo ใน conftest.py)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from app.pipeline.schemas import (
    AiStepRecord,
    ChatTurn,
    Conversation,
    Counters,
    Direction,
    EventType,
    FeedbackKind,
    OutboundMessage,
    OutboundStatus,
    ProcessStatus,
)
from app.repositories import Repos
from tests.repositories.conftest import Db

TTL = timedelta(hours=2)
T0 = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)


async def _insert(
    repos: Repos, wh: str = "wh1", user: str = "U1", occurred_at: datetime = T0
) -> int:
    rid = await repos.raw_events.insert(
        webhook_event_id=wh,
        line_user_id=user,
        event_type=EventType.MESSAGE,
        payload={"type": "message", "webhookEventId": wh},
        occurred_at=occurred_at,
    )
    assert rid is not None
    return rid


async def _conv(repos: Repos, line_user_id: str = "U1") -> Conversation:
    user = await repos.users.get_or_create(line_user_id)
    conv, _ = await repos.conversations.get_or_open(user.id, TTL)
    return conv


def _out(purpose: str = "ai_answer") -> OutboundMessage:
    return OutboundMessage(messages=[{"type": "text", "text": "สวัสดีค่ะ"}], purpose=purpose)


# --- raw events ---


async def test_raw_insert_dedupes_and_rejects_reply_token(repos: Repos) -> None:
    rid = await _insert(repos)
    assert (
        await repos.raw_events.insert(
            webhook_event_id="wh1",
            line_user_id="U1",
            event_type="message",
            payload={},
            occurred_at=T0,
        )
        is None
    )
    with pytest.raises(ValueError):
        await repos.raw_events.insert(
            webhook_event_id="wh2",
            line_user_id="U1",
            event_type="message",
            payload={"replyToken": "x"},
            occurred_at=T0,
        )
    row = await repos.raw_events.get(rid)
    assert row is not None
    assert (row.process_status, row.attempts, row.event_type) == (
        ProcessStatus.PENDING,
        0,
        "message",
    )
    assert row.payload == {"type": "message", "webhookEventId": "wh1"}
    assert row.occurred_at == T0
    assert await repos.raw_events.get(999_999) is None


async def test_db_check_rejects_reply_token_too(db: Db) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        await db.fetch(
            "INSERT INTO line_raw_events (webhook_event_id, event_type, payload, occurred_at)"
            " VALUES ('x', 'message', '{\"replyToken\": \"t\"}'::jsonb, now())"
        )


async def test_claim_only_once(repos: Repos) -> None:
    rid = await _insert(repos)
    first = await repos.raw_events.claim(rid)
    assert first is not None
    assert (first.process_status, first.attempts) == (ProcessStatus.PROCESSING, 1)
    assert first.claimed_at is not None
    assert await repos.raw_events.claim(rid) is None
    assert await repos.raw_events.claim(999_999) is None


async def test_concurrent_claim_one_winner(repos: Repos) -> None:
    rid = await _insert(repos)
    results = await asyncio.gather(*(repos.raw_events.claim(rid) for _ in range(3)))
    assert sum(r is not None for r in results) == 1


async def test_mark_done_failed_release(repos: Repos) -> None:
    rid = await _insert(repos)
    with pytest.raises(ValueError):
        await repos.raw_events.mark_done(rid, ProcessStatus.FAILED)
    assert await repos.raw_events.claim(rid) is not None
    await repos.raw_events.release(rid, "line_down")
    row = await repos.raw_events.get(rid)
    assert row is not None and (row.process_status, row.error) == (
        ProcessStatus.PENDING,
        "line_down",
    )
    await repos.raw_events.release(rid, "again")  # pending แล้ว -> ไม่เปลี่ยน
    row = await repos.raw_events.get(rid)
    assert row is not None and row.error == "line_down"
    again = await repos.raw_events.claim(rid)
    assert again is not None and again.attempts == 2  # attempts ไม่ลดตอน release
    await repos.raw_events.mark_done(rid, ProcessStatus.SKIPPED)
    row = await repos.raw_events.get(rid)
    assert row is not None
    assert (row.process_status, row.error) == (ProcessStatus.SKIPPED, None)
    assert row.processed_at is not None
    await repos.raw_events.mark_failed(rid, "boom")
    row = await repos.raw_events.get(rid)
    assert row is not None and (row.process_status, row.error) == (ProcessStatus.FAILED, "boom")


async def test_pending_ids_for_user_ordered(repos: Repos) -> None:
    late = await _insert(repos, "a", occurred_at=T0 + timedelta(seconds=5))
    early = await _insert(repos, "b", occurred_at=T0)
    claimed = await _insert(repos, "c", occurred_at=T0 - timedelta(seconds=5))
    await _insert(repos, "d", user="U2")
    assert await repos.raw_events.claim(claimed) is not None
    assert await repos.raw_events.list_pending_ids_for_user("U1") == [early, late]


async def test_list_stale_and_release_stuck(repos: Repos, db: Db) -> None:
    a = await _insert(repos, "a")
    b = await _insert(repos, "b")
    future = await db.now() + timedelta(minutes=1)
    assert await repos.raw_events.list_stale(future) == [a, b]
    assert await repos.raw_events.list_stale(future, limit=1) == [a]
    assert await repos.raw_events.list_stale(future - timedelta(hours=1)) == []
    assert await repos.raw_events.claim(a) is not None
    assert await repos.raw_events.release_stuck(future - timedelta(hours=1)) == []
    assert await repos.raw_events.release_stuck(future) == [a]
    row = await repos.raw_events.get(a)
    assert row is not None and row.process_status is ProcessStatus.PENDING
    assert await repos.raw_events.release_stuck(future) == []


async def test_delete_older_than_sets_null_refs(repos: Repos, db: Db) -> None:
    conv = await _conv(repos)
    old = await _insert(repos, "old")
    new = await _insert(repos, "new")
    mid = await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=old, message_type="text", text_redacted="x"
    )
    await db.fetch(
        "UPDATE line_raw_events SET received_at = now() - interval '91 days' WHERE id = %s", (old,)
    )
    cutoff = await db.now() - timedelta(days=90)
    assert await repos.raw_events.delete_older_than(cutoff) == 1
    assert await repos.raw_events.get(old) is None
    assert await repos.raw_events.get(new) is not None
    assert await db.scalar("SELECT raw_event_id FROM messages WHERE id = %s", (mid,)) is None


# --- users ---


async def test_get_or_create_and_display_name(repos: Repos) -> None:
    u1 = await repos.users.get_or_create("U1", "ก้อย")
    u2 = await repos.users.get_or_create("U1")
    assert u2.id == u1.id and u2.display_name == "ก้อย"
    assert u2.last_seen_at >= u1.last_seen_at and u2.created_at == u1.created_at
    u3 = await repos.users.get_or_create("U1", "Koi")
    assert u3.display_name == "Koi"
    assert (await repos.users.get_or_create("U2")).id != u1.id


async def test_has_consent_latest_row_wins(repos: Repos, db: Db) -> None:
    user = await repos.users.get_or_create("U1")
    assert await repos.users.has_consent(user.id, "privacy") is False
    ins = (
        "INSERT INTO line_consents (user_id, consent_type, granted, version, created_at)"
        " VALUES (%s, %s, %s, '1', %s)"
    )
    await db.fetch(ins, (user.id, "privacy", True, T0))
    assert await repos.users.has_consent(user.id, "privacy") is True
    await db.fetch(ins, (user.id, "privacy", False, T0 + timedelta(days=1)))
    await db.fetch(ins, (user.id, "marketing", True, T0 + timedelta(days=2)))
    assert await repos.users.has_consent(user.id, "privacy") is False
    assert await repos.users.has_consent(user.id, "marketing") is True


# --- messages ---


async def test_add_inbound_idempotent(repos: Repos) -> None:
    conv = await _conv(repos)
    rid = await _insert(repos)
    kw = dict(
        conversation_id=conv.id, raw_event_id=rid, message_type="text", text_redacted="หา [PHONE]"
    )
    m1 = await repos.messages.add_inbound(**kw)
    m2 = await repos.messages.add_inbound(**kw)
    assert m1 == m2


async def test_reserve_outbound_duplicate(repos: Repos, db: Db) -> None:
    conv = await _conv(repos)
    rid = await _insert(repos)
    kw = dict(
        conversation_id=conv.id,
        raw_event_id=rid,
        dedupe_key=f"out:{rid}:ai_answer",
        outbound=_out(),
    )
    r1 = await repos.messages.reserve_outbound(**kw, text_redacted="สวัสดีค่ะ")
    assert r1.already_sent is False
    row = await db.one(
        "SELECT direction, status, message_type, payload FROM messages WHERE id = %s",
        (r1.message_id,),
    )
    assert row == {
        "direction": "out",
        "status": "pending",
        "message_type": "ai_answer",
        "payload": [{"type": "text", "text": "สวัสดีค่ะ"}],
    }
    r2 = await repos.messages.reserve_outbound(**kw)
    assert (r2.message_id, r2.already_sent) == (r1.message_id, False)  # ยัง pending -> ส่งใหม่ได้
    await repos.messages.mark_outbound(r1.message_id, OutboundStatus.SENT)
    r3 = await repos.messages.reserve_outbound(**kw)
    assert (r3.message_id, r3.already_sent) == (r1.message_id, True)
    assert await db.scalar(
        "SELECT sent_at IS NOT NULL FROM messages WHERE id = %s", (r1.message_id,)
    )
    assert await db.scalar("SELECT count(*) FROM messages") == 1


async def test_mark_outbound_drop_reason_and_inbound_untouched(repos: Repos, db: Db) -> None:
    conv = await _conv(repos)
    rid = await _insert(repos)
    res = await repos.messages.reserve_outbound(
        conversation_id=conv.id, raw_event_id=None, dedupe_key="out:x", outbound=_out()
    )
    await repos.messages.mark_outbound(res.message_id, OutboundStatus.DROPPED, reason="human")
    row = await db.one(
        "SELECT status, drop_reason, sent_at FROM messages WHERE id = %s", (res.message_id,)
    )
    assert row == {"status": "dropped", "drop_reason": "human", "sent_at": None}
    mid = await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=rid, message_type="text", text_redacted="x"
    )
    await repos.messages.mark_outbound(mid, OutboundStatus.SENT)  # ขาเข้า -> ไม่แตะ
    assert await db.scalar("SELECT status FROM messages WHERE id = %s", (mid,)) is None


async def test_recent_texts_and_count(repos: Repos, db: Db) -> None:
    conv = await _conv(repos)
    since = await db.now()
    ids = [await _insert(repos, f"wh{i}") for i in range(4)]
    await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=ids[0], message_type="text", text_redacted="q1"
    )
    sent = await repos.messages.reserve_outbound(
        conversation_id=conv.id,
        raw_event_id=ids[0],
        dedupe_key="o1",
        outbound=_out(),
        text_redacted="a1",
    )
    await repos.messages.mark_outbound(sent.message_id, OutboundStatus.SENT)
    await repos.messages.reserve_outbound(  # pending -> ไม่นับ
        conversation_id=conv.id,
        raw_event_id=ids[1],
        dedupe_key="o2",
        outbound=_out(),
        text_redacted="a2",
    )
    await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=ids[1], message_type="image", text_redacted=None
    )
    await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=ids[2], message_type="text", text_redacted="q2"
    )
    await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=ids[3], message_type="text", text_redacted="q3"
    )
    turns = await repos.messages.recent_texts_redacted(conv.id, 4, exclude_raw_event_id=ids[3])
    assert turns == [
        ChatTurn(direction=Direction.IN, text_redacted="q1"),
        ChatTurn(direction=Direction.OUT, text_redacted="a1"),
        ChatTurn(direction=Direction.IN, text_redacted="q2"),
    ]
    assert [t.text_redacted for t in await repos.messages.recent_texts_redacted(conv.id, 2)] == [
        "q2",
        "q3",
    ]
    assert await repos.messages.recent_texts_redacted(conv.id, 0) == []
    # raw ถูกลบตาม retention (raw_event_id=NULL) ต้องยังเห็นข้อความเมื่อมี exclude
    await db.fetch("DELETE FROM line_raw_events WHERE id = %s", (ids[0],))
    turns = await repos.messages.recent_texts_redacted(conv.id, 4, exclude_raw_event_id=ids[3])
    assert turns[0] == ChatTurn(direction=Direction.IN, text_redacted="q1")
    assert await repos.messages.count_inbound_since(conv.id, since) == 4
    assert await repos.messages.count_inbound_since(conv.id, since + timedelta(hours=1)) == 0


async def test_recent_texts_exclude_types(repos: Repos, db: Db) -> None:
    conv = await _conv(repos)
    ids = [await _insert(repos, f"wx{i}") for i in range(3)]
    await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=ids[0], message_type="text", text_redacted="q1"
    )
    for i, (purpose, text) in enumerate([("ai_answer", "a1"), ("ai_down", "ขัดข้อง")]):
        res = await repos.messages.reserve_outbound(
            conversation_id=conv.id,
            raw_event_id=ids[i],
            dedupe_key=f"ox{i}",
            outbound=_out(purpose),
            text_redacted=text,
        )
        await repos.messages.mark_outbound(res.message_id, OutboundStatus.SENT)
    await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=ids[2], message_type="text", text_redacted="q2"
    )
    texts = [
        t.text_redacted
        for t in await repos.messages.recent_texts_redacted(conv.id, 3, exclude_types=["ai_down"])
    ]
    assert texts == ["q1", "a1", "q2"]  # ข้อความที่ถูกตัดไม่กินโควตา limit
    assert len(await repos.messages.recent_texts_redacted(conv.id, 4)) == 4


# --- ai runs / feedback / flags ---


async def test_ai_runs_idempotent_steps_finish(repos: Repos, db: Db) -> None:
    conv = await _conv(repos)
    rid = await _insert(repos)
    run = await repos.ai_runs.create_run(raw_event_id=rid, conversation_id=conv.id)
    assert await repos.ai_runs.create_run(raw_event_id=rid, conversation_id=conv.id) == run
    step = AiStepRecord(
        ai_run_id=run,
        raw_event_id=rid,
        seq=1,
        step="call_ai",
        input={"text": "[PHONE]"},
        output=None,
        latency_ms=120,
        cost=Decimal("0.0012"),
    )
    await repos.ai_runs.add_step(step)
    await repos.ai_runs.add_step(step)  # retry seq เดิมได้
    rows = await db.fetch(
        "SELECT seq, step, input, output, cost FROM ai_steps WHERE ai_run_id = %s", (run,)
    )
    assert len(rows) == 2
    assert rows[0] == {
        "seq": 1,
        "step": "call_ai",
        "input": {"text": "[PHONE]"},
        "output": None,
        "cost": Decimal("0.0012"),
    }
    await repos.ai_runs.finish_run(run, route="ai_answer", confidence=Decimal("0.8"))
    row = await db.one(
        "SELECT route, reason, confidence, finished_at IS NOT NULL AS done FROM ai_runs WHERE id = %s",
        (run,),
    )
    assert row == {"route": "ai_answer", "reason": None, "confidence": Decimal("0.8"), "done": True}


async def test_feedback_ai_run_id_validated(repos: Repos, db: Db) -> None:
    conv = await _conv(repos, "U1")
    other = await _conv(repos, "U2")
    run = await repos.ai_runs.create_run(
        raw_event_id=await _insert(repos, "a"), conversation_id=conv.id
    )
    other_run = await repos.ai_runs.create_run(
        raw_event_id=await _insert(repos, "b"), conversation_id=other.id
    )
    ok = await repos.feedback.add(
        conversation_id=conv.id, kind=FeedbackKind.THUMB_UP, value="up", ai_run_id=run
    )
    foreign = await repos.feedback.add(
        conversation_id=conv.id, kind=FeedbackKind.THUMB_DOWN, ai_run_id=other_run
    )
    missing = await repos.feedback.add(
        conversation_id=conv.id, kind=FeedbackKind.CARD_CLICK, ai_run_id=10**30
    )
    none = await repos.feedback.add(conversation_id=conv.id, kind=FeedbackKind.CARD_CLICK)
    rows = {r["id"]: r for r in await db.fetch("SELECT id, ai_run_id, kind, value FROM feedback")}
    assert rows[ok] == {"id": ok, "ai_run_id": run, "kind": "thumb_up", "value": "up"}
    assert rows[foreign]["ai_run_id"] is None and rows[foreign]["kind"] == "thumb_down"
    assert rows[missing]["ai_run_id"] is None and rows[none]["ai_run_id"] is None


async def test_flags_upsert(repos: Repos) -> None:
    assert await repos.flags.get_flag("kill_switch") is None
    await repos.flags.set_flag("kill_switch", "on")
    await repos.flags.set_flag("kill_switch", "off")
    assert await repos.flags.get_flag("kill_switch") == "off"


# --- PDPA ---


async def test_pdpa_delete_user_cascades(repos: Repos, db: Db) -> None:
    conv = await _conv(repos)
    user_id = conv.user_id
    rid = await _insert(repos)
    await db.fetch(
        "INSERT INTO line_consents (user_id, consent_type, granted, version) VALUES (%s, 'privacy', true, '1')",
        (user_id,),
    )
    await repos.state.save(conv.id, state={"a": 1}, counters=Counters(), expires_at=T0)
    mid = await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=rid, message_type="image", text_redacted=None
    )
    await repos.messages.add_attachment(
        message_id=mid, storage_key=f"images/{conv.id}/m1", content_type="image/jpeg", size_bytes=10
    )
    run = await repos.ai_runs.create_run(raw_event_id=rid, conversation_id=conv.id)
    await repos.ai_runs.add_step(
        AiStepRecord(ai_run_id=run, raw_event_id=rid, seq=1, step="call_ai")
    )
    await repos.feedback.add(conversation_id=conv.id, kind=FeedbackKind.THUMB_UP, ai_run_id=run)
    started = await repos.conversations.start_handoff(conv.id, "x")
    assert started is not None and started.handoff_started_at is not None
    await repos.conversations.create_close_token(conv.id, started.handoff_started_at, TTL)

    await db.fetch("DELETE FROM line_users WHERE id = %s", (user_id,))

    for table in (
        "line_users",
        "line_consents",
        "conversations",
        "conversation_state",
        "messages",
        "attachments",
        "ai_runs",
        "ai_steps",
        "feedback",
        "close_tokens",
    ):
        assert await db.scalar(f"SELECT count(*) FROM {table}") == 0, table
    # line_raw_events ไม่มี FK ไป line_users -> ต้องลบตาม line_user_id แยก
    assert await db.scalar("SELECT count(*) FROM line_raw_events") == 1
