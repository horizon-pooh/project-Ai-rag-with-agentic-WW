"""tests/repositories/test_pg_reply_tokens.py — PgReplyTokens บน Postgres จริง (D-010: replyToken ย้ายจาก Redis)

ข้ามทั้งไฟล์ถ้าไม่ได้ตั้ง TEST_DATABASE_URL (fixture db_conninfo ใน conftest.py)
SQL ในไฟล์นี้ใช้เตรียม/ตรวจข้อมูลเท่านั้น (เช่น ย้อน expires_at ให้หมดอายุ)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.pipeline.ports import ReplyTokens
from app.repositories import PgReplyTokens, Repos
from tests.repositories.conftest import Db

T0 = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)


async def _raw(repos: Repos, wh: str, user: str = "U1") -> int:
    rid = await repos.raw_events.insert(
        webhook_event_id=wh,
        line_user_id=user,
        event_type="message",
        payload={"type": "message"},
        occurred_at=T0,
    )
    assert rid is not None
    return rid


async def test_put_take_is_single_use_with_ttl(pool: AsyncConnectionPool, repos: Repos, db: Db) -> None:
    tokens = PgReplyTokens(pool, ttl_s=50)
    assert isinstance(tokens, ReplyTokens)
    rid = await _raw(repos, "a")
    await tokens.put(rid, "rt-secret")
    row = await db.one("SELECT token, expires_at - now() AS ttl FROM reply_tokens WHERE raw_event_id = %s", (rid,))
    assert row["token"] == "rt-secret"
    # expires_at = now() + REPLY_TOKEN_TTL_S ตามนาฬิกาของ DB
    assert timedelta(seconds=49) < row["ttl"] <= timedelta(seconds=50)
    assert await tokens.take(rid) == "rt-secret"
    # ใช้ได้ครั้งเดียว: แถวถูกลบ
    assert await tokens.take(rid) is None
    assert await db.scalar("SELECT count(*) FROM reply_tokens") == 0


async def test_take_missing_returns_none(pool: AsyncConnectionPool) -> None:
    assert await PgReplyTokens(pool, ttl_s=50).take(12345) is None


async def test_expired_token_not_returned_then_cleaned(
    pool: AsyncConnectionPool, repos: Repos, db: Db
) -> None:
    tokens = PgReplyTokens(pool, ttl_s=50)
    old = await _raw(repos, "a")
    fresh = await _raw(repos, "b")
    await tokens.put(old, "rt-old")
    await tokens.put(fresh, "rt-fresh")
    await db.fetch(
        "UPDATE reply_tokens SET expires_at = now() - interval '1 second' WHERE raw_event_id = %s",
        (old,),
    )
    # หมดอายุ -> None (ผู้เรียกใช้ push) แถวยังอยู่จนกว่า retention ลบ
    assert await tokens.take(old) is None
    assert await db.scalar("SELECT count(*) FROM reply_tokens WHERE raw_event_id = %s", (old,)) == 1
    assert await tokens.delete_expired() == 1
    assert await tokens.delete_expired() == 0
    # ตัวที่ยังไม่หมดอายุไม่โดนลบ
    assert await tokens.take(fresh) == "rt-fresh"


async def test_delete_expired_respects_limit(pool: AsyncConnectionPool, repos: Repos, db: Db) -> None:
    tokens = PgReplyTokens(pool, ttl_s=50)
    for i in range(3):
        await tokens.put(await _raw(repos, f"e{i}"), f"rt-{i}")
    await db.fetch("UPDATE reply_tokens SET expires_at = now() - interval '1 minute'")
    assert await tokens.delete_expired(limit=2) == 2
    assert await tokens.delete_expired(limit=2) == 1


async def test_put_again_replaces_token(pool: AsyncConnectionPool, repos: Repos) -> None:
    tokens = PgReplyTokens(pool, ttl_s=50)
    rid = await _raw(repos, "a")
    await tokens.put(rid, "rt-1")
    await tokens.put(rid, "rt-2")
    assert await tokens.take(rid) == "rt-2"


async def test_token_requires_existing_raw_event(pool: AsyncConnectionPool) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await PgReplyTokens(pool, ttl_s=50).put(999_999, "rt")


async def test_deleting_raw_event_cascades(pool: AsyncConnectionPool, repos: Repos, db: Db) -> None:
    tokens = PgReplyTokens(pool, ttl_s=50)
    rid = await _raw(repos, "a", user="U-pdpa")
    await tokens.put(rid, "rt")
    # PDPA: ลบ raw events ของ user -> replyToken หายตาม (FK ON DELETE CASCADE)
    await repos.users.delete_user("U-pdpa")
    assert await db.scalar("SELECT count(*) FROM reply_tokens") == 0


async def test_reply_token_never_in_raw_payload(repos: Repos) -> None:
    # CHECK line_raw_events_no_reply_token ยังอยู่ (token อยู่ตาราง reply_tokens เท่านั้น)
    with pytest.raises(ValueError):
        await repos.raw_events.insert(
            webhook_event_id="x",
            line_user_id="U1",
            event_type="message",
            payload={"replyToken": "rt"},
            occurred_at=T0,
        )
