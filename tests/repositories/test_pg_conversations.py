"""tests/repositories/test_pg_conversations.py — PgConversationsRepo / PgStateRepo บน Postgres จริง

ข้ามทั้งไฟล์ถ้าไม่ได้ตั้ง TEST_DATABASE_URL (fixture db_conninfo ใน conftest.py)
เน้นเงื่อนไขของสัญญาใน app/pipeline/ports.py: get_or_open (human ไม่เปิดใหม่ / bot เงียบเกินเปิดใหม่ / ชนกัน),
start_handoff / end_handoff แบบมีเงื่อนไข, close token ใช้ครั้งเดียว/หมดอายุ
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

from app.pipeline.schemas import (
    Conversation,
    ConversationStatus,
    Counters,
    HandoffEndReason,
    Product,
    hash_token,
)
from app.repositories import Repos
from tests.repositories.conftest import Db

TTL = timedelta(hours=2)


async def _open(repos: Repos, line_user_id: str = "U1") -> Conversation:
    user = await repos.users.get_or_create(line_user_id)
    conv, reopened = await repos.conversations.get_or_open(user.id, TTL)
    assert reopened is False
    return conv


async def _human(repos: Repos) -> Conversation:
    conv = await _open(repos)
    started = await repos.conversations.start_handoff(conv.id, "rule_escalate")
    assert started is not None
    return started


async def _age(db: Db, conv_id: int, **cols: timedelta) -> None:
    """เลื่อนเวลาในแถวย้อนหลัง เช่น _age(db, id, opened_at=3h) -> opened_at = now() - 3h"""
    sets = ", ".join(f"{c} = now() - %s::interval" for c in cols)
    await db.fetch(f"UPDATE conversations SET {sets} WHERE id = %s", (*cols.values(), conv_id))


# --- get_or_open ---


async def test_get_or_open_creates_then_returns_same(repos: Repos) -> None:
    conv = await _open(repos)
    assert conv.status is ConversationStatus.BOT and conv.closed_at is None
    again, reopened = await repos.conversations.get_or_open(conv.user_id, TTL)
    assert (again.id, reopened) == (conv.id, False)
    assert await repos.conversations.get(conv.id) == again
    assert await repos.conversations.get(999_999) is None


async def test_bot_idle_reopens(repos: Repos, db: Db) -> None:
    conv = await _open(repos)
    await _age(db, conv.id, opened_at=timedelta(hours=3))
    new, reopened = await repos.conversations.get_or_open(conv.user_id, TTL)
    assert reopened is True and new.id != conv.id and new.status is ConversationStatus.BOT
    old = await repos.conversations.get(conv.id)
    assert old is not None and old.closed_at is not None


async def test_bot_recent_user_message_keeps_open(repos: Repos, db: Db) -> None:
    conv = await _open(repos)
    await _age(db, conv.id, opened_at=timedelta(hours=3))
    await repos.conversations.touch_user_message(conv.id)  # greatest(...) = ตอนนี้
    same, reopened = await repos.conversations.get_or_open(conv.user_id, TTL)
    assert (same.id, reopened) == (conv.id, False)
    assert same.last_user_message_at is not None


async def test_human_never_reopens(repos: Repos, db: Db) -> None:
    conv = await _human(repos)
    await _age(db, conv.id, opened_at=timedelta(days=3), handoff_started_at=timedelta(days=3))
    same, reopened = await repos.conversations.get_or_open(conv.user_id, TTL)
    assert (same.id, reopened, same.status) == (conv.id, False, ConversationStatus.HUMAN)
    assert same.closed_at is None


async def test_concurrent_get_or_open_single_conversation(repos: Repos, db: Db) -> None:
    user = await repos.users.get_or_create("U-race")
    results = await asyncio.gather(
        *(repos.conversations.get_or_open(user.id, TTL) for _ in range(3))
    )
    assert len({c.id for c, _ in results}) == 1
    assert all(r is False for _, r in results)
    assert await db.scalar("SELECT count(*) FROM conversations WHERE user_id = %s", (user.id,)) == 1


# --- handoff ---


async def test_start_handoff_only_once(repos: Repos) -> None:
    conv = await _human(repos)
    assert conv.status is ConversationStatus.HUMAN and conv.handoff_reason == "rule_escalate"
    assert conv.handoff_started_at is not None
    assert await repos.conversations.start_handoff(conv.id, "again") is None


async def test_start_handoff_on_closed_conversation_is_noop(repos: Repos, db: Db) -> None:
    conv = await _open(repos)
    await _age(db, conv.id, opened_at=timedelta(hours=3))
    await repos.conversations.get_or_open(conv.user_id, TTL)  # ปิดอันเดิม
    assert await repos.conversations.start_handoff(conv.id, "late") is None


async def test_end_handoff_once_and_clears_state(repos: Repos) -> None:
    conv = await _human(repos)
    assert conv.handoff_started_at is not None
    await repos.state.save(
        conv.id, state={"q": "x"}, counters=Counters(), expires_at=conv.handoff_started_at + TTL
    )
    ended = await repos.conversations.end_handoff(
        conv.id, HandoffEndReason.USER_RESUMED, conv.handoff_started_at
    )
    assert ended is not None
    assert ended.status is ConversationStatus.BOT
    assert ended.handoff_end_reason is HandoffEndReason.USER_RESUMED
    assert ended.handoff_ended_at is not None
    assert await repos.state.get(conv.id) is None
    # ครั้งที่สอง (เช่น cron timeout มาทีหลัง) -> None
    assert (
        await repos.conversations.end_handoff(
            conv.id, HandoffEndReason.TIMEOUT, conv.handoff_started_at
        )
        is None
    )


async def test_concurrent_end_handoff_one_winner(repos: Repos) -> None:
    conv = await _human(repos)
    assert conv.handoff_started_at is not None
    results = await asyncio.gather(
        repos.conversations.end_handoff(
            conv.id, HandoffEndReason.USER_RESUMED, conv.handoff_started_at
        ),
        repos.conversations.end_handoff(conv.id, HandoffEndReason.TIMEOUT, conv.handoff_started_at),
    )
    assert sum(r is not None for r in results) == 1


async def test_end_handoff_stale_round_is_noop(repos: Repos) -> None:
    first = await _human(repos)
    assert first.handoff_started_at is not None
    ended = await repos.conversations.end_handoff(
        first.id, HandoffEndReason.USER_RESUMED, first.handoff_started_at
    )
    assert ended is not None
    second = await repos.conversations.start_handoff(first.id, "rule_escalate")
    assert (
        second is not None and second.handoff_ended_at is None and second.handoff_end_reason is None
    )
    # คำสั่งของรอบเก่า (เช่นลิงก์ /close เก่า) ต้องไม่ปิดรอบใหม่
    assert (
        await repos.conversations.end_handoff(
            first.id, HandoffEndReason.STAFF_CLOSED, first.handoff_started_at
        )
        is None
    )
    still = await repos.conversations.get(first.id)
    assert still is not None and still.status is ConversationStatus.HUMAN


async def test_list_handoff_timed_out(repos: Repos, db: Db) -> None:
    a = await _human(repos)
    b_user = await repos.users.get_or_create("U2")
    b, _ = await repos.conversations.get_or_open(b_user.id, TTL)
    b = await repos.conversations.start_handoff(b.id, "x")
    assert b is not None
    await _age(db, a.id, handoff_started_at=timedelta(hours=7))
    timed_out = await repos.conversations.list_handoff_timed_out(timedelta(hours=6))
    assert [c.id for c in timed_out] == [a.id]
    assert await repos.conversations.list_handoff_timed_out(timedelta(hours=6), limit=0) == []


async def test_list_open_human_newest_first(repos: Repos, db: Db) -> None:
    a = await _human(repos)
    b_user = await repos.users.get_or_create("U2")
    b, _ = await repos.conversations.get_or_open(b_user.id, TTL)
    b = await repos.conversations.start_handoff(b.id, "x")
    assert b is not None
    c_user = await repos.users.get_or_create("U3")
    await repos.conversations.get_or_open(c_user.id, TTL)  # bot -> ไม่อยู่ในรายการ
    await _age(db, a.id, handoff_started_at=timedelta(hours=7))
    assert [c.id for c in await repos.conversations.list_open_human()] == [b.id, a.id]
    assert [c.id for c in await repos.conversations.list_open_human(limit=1)] == [b.id]


# --- close token ---


async def test_close_token_single_use_and_hash_only(repos: Repos, db: Db) -> None:
    conv = await _human(repos)
    assert conv.handoff_started_at is not None
    token = await repos.conversations.create_close_token(
        conv.id, conv.handoff_started_at, timedelta(hours=24)
    )
    stored = await db.fetch("SELECT token_hash FROM close_tokens")
    assert stored == [{"token_hash": hash_token(token)}]
    got = await repos.conversations.consume_close_token(token)
    assert got is not None
    assert (got.conversation_id, got.handoff_started_at) == (conv.id, conv.handoff_started_at)
    assert await repos.conversations.consume_close_token(token) is None
    assert await repos.conversations.consume_close_token("not-a-token") is None
    ended = await repos.conversations.end_handoff(
        got.conversation_id, HandoffEndReason.STAFF_CLOSED, got.handoff_started_at
    )
    assert ended is not None and ended.handoff_end_reason is HandoffEndReason.STAFF_CLOSED


async def test_close_token_expired(repos: Repos) -> None:
    conv = await _human(repos)
    assert conv.handoff_started_at is not None
    token = await repos.conversations.create_close_token(
        conv.id, conv.handoff_started_at, timedelta(seconds=-1)
    )
    assert await repos.conversations.consume_close_token(token) is None


async def test_concurrent_consume_close_token_one_winner(repos: Repos) -> None:
    conv = await _human(repos)
    assert conv.handoff_started_at is not None
    token = await repos.conversations.create_close_token(
        conv.id, conv.handoff_started_at, timedelta(hours=1)
    )
    results = await asyncio.gather(
        *(repos.conversations.consume_close_token(token) for _ in range(3))
    )
    assert sum(r is not None for r in results) == 1


# --- state ---


async def test_state_upsert_roundtrip_and_clear(repos: Repos, db: Db) -> None:
    conv = await _open(repos)
    now = await db.now()
    assert await repos.state.get(conv.id) is None
    counters = Counters(
        clarify_streak=1,
        last_products=[
            Product(sku="A1", name="สว่าน", price=Decimal("1290.50"), url="https://x/a1")
        ],
    )
    await repos.state.save(
        conv.id, state={"need": "drill"}, counters=counters, expires_at=now + TTL
    )
    rec = await repos.state.get(conv.id)
    assert rec is not None
    assert (rec.state, rec.counters, rec.expires_at) == ({"need": "drill"}, counters, now + TTL)
    # UPSERT ทับทั้งแถว; state=None -> NULL
    await repos.state.save(conv.id, state=None, counters=Counters(), expires_at=now - TTL)
    rec = await repos.state.get(conv.id)
    assert rec is not None and rec.state is None and rec.counters == Counters()
    assert rec.is_expired(now)  # คืนแถวแม้หมดอายุ (prepare ตัดสินเอง)
    await repos.state.clear(conv.id)
    await repos.state.clear(conv.id)  # ไม่มีแถวก็ไม่ error
    assert await repos.state.get(conv.id) is None
