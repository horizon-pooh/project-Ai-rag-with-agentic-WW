"""tests/repositories/test_pg_retention_pdpa.py — ลบตาม PDPA และ SQL ของ retention บน Postgres จริง (R2-A)

ข้ามทั้งไฟล์ถ้าไม่ได้ตั้ง TEST_DATABASE_URL (fixture db_conninfo ใน conftest.py)
SQL ในไฟล์นี้ใช้เตรียม/ตรวจข้อมูลเท่านั้น (เช่น ย้อน created_at ให้เก่า)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.pipeline.schemas import (
    AiStepRecord,
    AttachmentRef,
    Conversation,
    Counters,
    EventType,
    FeedbackKind,
    OutboundMessage,
)
from app.repositories import Repos
from tests.repositories.conftest import Db

TTL = timedelta(hours=2)
T0 = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)

# ตารางที่ cascade จาก line_users
CASCADE_TABLES = (
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
)


async def _insert(repos: Repos, wh: str, user: str) -> int:
    rid = await repos.raw_events.insert(
        webhook_event_id=wh,
        line_user_id=user,
        event_type=EventType.MESSAGE,
        payload={"type": "message"},
        occurred_at=T0,
    )
    assert rid is not None
    return rid


async def _conv(repos: Repos, line_user_id: str) -> Conversation:
    user = await repos.users.get_or_create(line_user_id)
    conv, _ = await repos.conversations.get_or_open(user.id, TTL)
    return conv


async def _seed(repos: Repos, db: Db, line_user_id: str, tag: str) -> dict[str, int | str]:
    """ข้อมูลครบทุกตารางของ user หนึ่งคน (ข้อความมีรูปหนึ่งไฟล์)"""
    conv = await _conv(repos, line_user_id)
    rid = await _insert(repos, f"wh-{tag}", line_user_id)
    await db.fetch(
        "INSERT INTO line_consents (user_id, consent_type, granted, version) VALUES (%s, 'privacy', true, '1')",
        (conv.user_id,),
    )
    await repos.state.save(conv.id, state={"a": 1}, counters=Counters(), expires_at=T0)
    mid = await repos.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=rid, message_type="image", text_redacted="ดูรูป"
    )
    key = f"images/{conv.id}/{tag}"
    aid = await repos.messages.add_attachment(
        message_id=mid, storage_key=key, content_type="image/jpeg", size_bytes=10
    )
    await repos.messages.reserve_outbound(
        conversation_id=conv.id,
        raw_event_id=rid,
        dedupe_key=f"out:{rid}:ai_answer",
        outbound=OutboundMessage(messages=[{"type": "text", "text": "ตอบ"}], purpose="ai_answer"),
        text_redacted="ตอบ",
    )
    run = await repos.ai_runs.create_run(raw_event_id=rid, conversation_id=conv.id)
    await repos.ai_runs.add_step(
        AiStepRecord(
            ai_run_id=run,
            raw_event_id=rid,
            seq=1,
            step="call_ai",
            input={"t": "x"},
            output={"o": 1},
            state_before={"s": 0},
            state_after={"s": 1},
            latency_ms=7,
        )
    )
    await repos.feedback.add(conversation_id=conv.id, kind=FeedbackKind.THUMB_UP, ai_run_id=run)
    started = await repos.conversations.start_handoff(conv.id, "x")
    assert started is not None and started.handoff_started_at is not None
    await repos.conversations.create_close_token(conv.id, started.handoff_started_at, TTL)
    return {"user_id": conv.user_id, "conv_id": conv.id, "key": key, "attachment_id": aid}


# --- users: get_line_user_id ---


async def test_get_line_user_id(repos: Repos, db: Db) -> None:
    conv = await _conv(repos, "U1")
    assert await repos.users.get_line_user_id(conv.user_id) == "U1"
    assert await repos.users.get_line_user_id(999_999) is None
    await db.fetch("UPDATE line_users SET deleted_at = now() WHERE id = %s", (conv.user_id,))
    assert await repos.users.get_line_user_id(conv.user_id) is None


# --- PDPA ---


async def test_delete_user_removes_everything_of_that_user(repos: Repos, db: Db) -> None:
    mine = await _seed(repos, db, "U1", "a")
    other = await _seed(repos, db, "U2", "b")
    # raw event ที่ไม่มีแถว user ผูก (เช่น event ก่อนสร้าง user) ก็ต้องหาย
    await _insert(repos, "wh-a2", "U1")

    keys = await repos.users.delete_user("U1")

    assert keys == [mine["key"]]
    assert await db.scalar("SELECT count(*) FROM line_raw_events WHERE line_user_id = 'U1'") == 0
    assert await db.scalar("SELECT count(*) FROM line_raw_events WHERE line_user_id = 'U2'") == 1
    for table in CASCADE_TABLES:
        # เหลือแต่ของ U2 ครบทุกตาราง (ตารางละ 1 แถว ยกเว้น messages = ขาเข้า + ขาออก)
        expected = 2 if table == "messages" else 1
        assert await db.scalar(f"SELECT count(*) FROM {table}") == expected, table
    assert await db.scalar("SELECT user_id FROM conversations") == other["user_id"]
    assert await db.scalar("SELECT storage_key FROM attachments") == other["key"]


async def test_delete_user_skips_already_deleted_files(repos: Repos, db: Db) -> None:
    seed = await _seed(repos, db, "U1", "a")
    await repos.messages.mark_attachment_deleted(int(seed["attachment_id"]))
    assert await repos.users.delete_user("U1") == []
    assert await db.scalar("SELECT count(*) FROM line_users") == 0


async def test_delete_unknown_user_returns_none_but_deletes_raw(repos: Repos, db: Db) -> None:
    await _insert(repos, "wh-x", "U-ghost")
    assert await repos.users.delete_user("U-ghost") is None
    assert await db.scalar("SELECT count(*) FROM line_raw_events") == 0
    assert await repos.users.delete_user("U-ghost") is None


# --- retention: รูป ---


async def test_list_and_mark_expired_attachments(repos: Repos, db: Db) -> None:
    old = await _seed(repos, db, "U1", "old")
    new = await _seed(repos, db, "U2", "new")
    await db.fetch(
        "UPDATE attachments SET created_at = now() - interval '31 days' WHERE id = %s",
        (old["attachment_id"],),
    )
    cutoff = await db.now() - timedelta(days=30)

    rows = await repos.messages.list_expired_attachments(cutoff, limit=10)
    assert rows == [AttachmentRef(id=int(old["attachment_id"]), storage_key=str(old["key"]))]
    assert await repos.messages.list_expired_attachments(cutoff, limit=0) == []

    await repos.messages.mark_attachment_deleted(int(old["attachment_id"]))
    first = await db.scalar(
        "SELECT deleted_at FROM attachments WHERE id = %s", (old["attachment_id"],)
    )
    assert first is not None
    await repos.messages.mark_attachment_deleted(int(old["attachment_id"]))  # ซ้ำ -> ไม่เลื่อนเวลา
    assert (
        await db.scalar("SELECT deleted_at FROM attachments WHERE id = %s", (old["attachment_id"],))
        == first
    )
    assert await repos.messages.list_expired_attachments(cutoff, limit=10) == []
    assert (
        await db.scalar("SELECT deleted_at FROM attachments WHERE id = %s", (new["attachment_id"],))
        is None
    )


# --- retention: anonymize ---


async def test_anonymize_messages_and_steps_batched_idempotent(repos: Repos, db: Db) -> None:
    old = await _seed(repos, db, "U1", "old")
    await _seed(repos, db, "U2", "new")
    await db.fetch(
        "UPDATE messages SET created_at = now() - interval '13 months' WHERE conversation_id = %s",
        (old["conv_id"],),
    )
    await db.fetch(
        "UPDATE ai_steps SET created_at = now() - interval '13 months'"
        " WHERE ai_run_id IN (SELECT id FROM ai_runs WHERE conversation_id = %s)",
        (old["conv_id"],),
    )
    cutoff = await db.now() - timedelta(days=365)

    # ทีละก้อน: limit=1 -> 1 แถว แล้วอีก 1 แถว แล้ว 0
    assert await repos.messages.anonymize_older_than(cutoff, limit=1) == 1
    assert await repos.messages.anonymize_older_than(cutoff, limit=1) == 1
    assert await repos.messages.anonymize_older_than(cutoff, limit=1) == 0
    assert await repos.ai_runs.anonymize_steps_older_than(cutoff, limit=10) == 1
    assert await repos.ai_runs.anonymize_steps_older_than(cutoff, limit=10) == 0

    rows = await db.fetch(
        "SELECT conversation_id, text_redacted, payload FROM messages ORDER BY id"
    )
    for r in rows:
        if r["conversation_id"] == old["conv_id"]:
            assert (r["text_redacted"], r["payload"]) == (None, None)
        else:
            assert r["text_redacted"] is not None
    steps = await db.fetch(
        "SELECT s.input, s.output, s.state_before, s.state_after, s.latency_ms, r.conversation_id"
        " FROM ai_steps AS s JOIN ai_runs AS r ON r.id = s.ai_run_id"
    )
    for s in steps:
        if s["conversation_id"] == old["conv_id"]:
            assert (s["input"], s["output"], s["state_before"], s["state_after"]) == (None,) * 4
            assert s["latency_ms"] == 7
        else:
            assert s["input"] == {"t": "x"}
