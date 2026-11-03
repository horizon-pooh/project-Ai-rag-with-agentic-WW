"""app/repositories/conversations.py

ตาราง conversations (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - หา conversation ที่เปิดอยู่ / เปิดใหม่ (bot เงียบเกิน 2 ชม.; human ห้ามเปิดใหม่)
 - start_handoff: UPDATE ... SET status='human', handoff_started_at=now(), handoff_reason WHERE status='bot'
 - end_handoff: UPDATE ... SET status='bot', handoff_ended_at=now(), handoff_end_reason=$2 WHERE id=$1 AND status='human' AND handoff_started_at=$3 RETURNING *
 - หา human ที่เกิน HANDOFF_TTL_HOURS (ให้ cron handoff_timeout)
 - list_open_human: human ทั้งหมด ล่าสุดก่อน (ให้ `python -m app.worker.admin conversations --status human`)
 - token ลิงก์ /close (ใช้ครั้งเดียว)
 - outcome, outcome_source, staff_label (ข้อเสนอ หัวข้อ นิยามความสำเร็จและการวัด)

แนวคิดจาก: src/store/redisStore.js, src/conversation/stateMachine.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 7: PgConversationsRepo ตาม ConversationsRepo; outcome/staff_label ยังไม่มีเมธอดเขียน)
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from app.pipeline.schemas import (
    CloseToken,
    Conversation,
    ConversationStatus,
    HandoffEndReason,
    hash_token,
)
from app.repositories.db import columns

CONVERSATION_COLUMNS = (
    "id",
    "user_id",
    "status",
    "opened_at",
    "last_user_message_at",
    "closed_at",
    "handoff_started_at",
    "handoff_reason",
    "handoff_ended_at",
    "handoff_end_reason",
    "outcome",
    "outcome_source",
    "staff_label",
)
_COLS = columns(*CONVERSATION_COLUMNS)

# ความยาว token ลิงก์ /close (ไบต์สุ่มก่อน base64)
CLOSE_TOKEN_BYTES = 32


def _conv(row: dict[str, Any]) -> Conversation:
    return Conversation.model_validate(row)


class PgConversationsRepo:
    """ConversationsRepo บน Postgres — จุดเดียวที่เปลี่ยน status bot <-> human"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get(self, conversation_id: int) -> Conversation | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM conversations WHERE id = %s", (conversation_id,)
            )
            row = await cur.fetchone()
        return None if row is None else _conv(row)

    async def get_or_open(self, user_id: int, idle_ttl: timedelta) -> tuple[Conversation, bool]:
        async with self._pool.connection() as conn, conn.transaction():
            # รอบที่ 2 มีไว้เคสชน unique index (อีก transaction เปิดให้ user เดียวกันพร้อมกัน) -> อ่านใหม่ครั้งเดียว
            for attempt in range(2):
                # FOR UPDATE: ล็อกแถวที่เปิดอยู่ กัน start_handoff แทรกระหว่างตัดสินปิด/เปิดใหม่
                cur = await conn.execute(
                    f"""
                    SELECT {_COLS},
                           greatest(opened_at, last_user_message_at, handoff_ended_at)
                               <= now() - %s::interval AS idle
                    FROM conversations
                    WHERE user_id = %s AND closed_at IS NULL
                    FOR UPDATE
                    """,
                    (idle_ttl, user_id),
                )
                row = await cur.fetchone()
                if row is None:
                    try:
                        async with conn.transaction():  # savepoint: ชนแล้ว transaction หลักยังใช้ต่อได้
                            return await self._insert(conn, user_id), False
                    except UniqueViolation:
                        if attempt:
                            raise
                        continue
                idle = bool(row.pop("idle"))
                conv = _conv(row)
                if conv.status is ConversationStatus.HUMAN or not idle:
                    # human ห้ามเปิดใหม่ทับเคสที่พนักงานดูแล ไม่ว่าเงียบนานแค่ไหน
                    return conv, False
                await conn.execute(
                    """
                    UPDATE conversations SET closed_at = now()
                    WHERE id = %s AND status = 'bot' AND closed_at IS NULL
                    """,
                    (conv.id,),
                )
                return await self._insert(conn, user_id), True
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    async def _insert(conn: AsyncConnection[Any], user_id: int) -> Conversation:
        cur = await conn.execute(
            f"INSERT INTO conversations (user_id) VALUES (%s) RETURNING {_COLS}", (user_id,)
        )
        row = await cur.fetchone()
        assert row is not None
        return _conv(row)

    async def touch_user_message(self, conversation_id: int) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE conversations SET last_user_message_at = now() WHERE id = %s",
                (conversation_id,),
            )

    async def start_handoff(self, conversation_id: int, reason: str) -> Conversation | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""
                UPDATE conversations
                SET status = 'human', handoff_started_at = now(), handoff_reason = %s,
                    handoff_ended_at = NULL, handoff_end_reason = NULL
                WHERE id = %s AND status = 'bot' AND closed_at IS NULL
                RETURNING {_COLS}
                """,
                (reason, conversation_id),
            )
            row = await cur.fetchone()
        return None if row is None else _conv(row)

    async def end_handoff(
        self, conversation_id: int, reason: HandoffEndReason, expected_started_at: datetime
    ) -> Conversation | None:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                f"""
                UPDATE conversations
                SET status = 'bot', handoff_ended_at = now(), handoff_end_reason = %s
                WHERE id = %s AND status = 'human' AND handoff_started_at = %s
                RETURNING {_COLS}
                """,
                (HandoffEndReason(reason).value, conversation_id, expected_started_at),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            # transaction เดียวกัน: ล้าง state (กลับมาคุยกับ AI เริ่มใหม่)
            await conn.execute(
                "DELETE FROM conversation_state WHERE conversation_id = %s", (conversation_id,)
            )
        return _conv(row)

    async def list_handoff_timed_out(self, ttl: timedelta, limit: int = 100) -> list[Conversation]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""
                SELECT {_COLS} FROM conversations
                WHERE status = 'human' AND handoff_started_at <= now() - %s::interval
                ORDER BY handoff_started_at, id
                LIMIT %s
                """,
                (ttl, limit),
            )
            rows = await cur.fetchall()
        return [_conv(r) for r in rows]

    async def list_open_human(self, limit: int = 50) -> list[Conversation]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""
                SELECT {_COLS} FROM conversations
                WHERE status = 'human'
                ORDER BY handoff_started_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cur.fetchall()
        return [_conv(r) for r in rows]

    async def create_close_token(
        self, conversation_id: int, handoff_started_at: datetime, ttl: timedelta
    ) -> str:
        token = secrets.token_urlsafe(CLOSE_TOKEN_BYTES)
        async with self._pool.connection() as conn:
            # เก็บแค่ hash — token จริงห้ามลง DB/log
            await conn.execute(
                """
                INSERT INTO close_tokens (token_hash, conversation_id, handoff_started_at, expires_at)
                VALUES (%s, %s, %s, now() + %s::interval)
                """,
                (hash_token(token), conversation_id, handoff_started_at, ttl),
            )
        return token

    async def consume_close_token(self, token: str) -> CloseToken | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE close_tokens SET used_at = now()
                WHERE token_hash = %s AND used_at IS NULL AND expires_at > now()
                RETURNING conversation_id, handoff_started_at
                """,
                (hash_token(token),),
            )
            row = await cur.fetchone()
        return None if row is None else CloseToken.model_validate(row)


__all__ = ["CONVERSATION_COLUMNS", "PgConversationsRepo"]
