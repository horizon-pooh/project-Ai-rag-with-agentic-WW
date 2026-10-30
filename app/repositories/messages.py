"""app/repositories/messages.py

ตาราง messages และ attachments (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - บันทึกข้อความขาเข้า/ขาออก (เก็บ text_redacted สำหรับส่ง AI)
 - สถานะขาออก: บันทึกก่อนส่ง (กันส่งซ้ำ) แล้วอัปเดตผล sent / dropped / failed + เหตุผล
 - ดึงข้อความล่าสุด 2-4 ข้อความ (ส่ง AI)
 - attachments: key ใน MinIO; retention รูป: list_expired_attachments -> (ผู้เรียกลบไฟล์) -> mark_attachment_deleted
 - anonymize เมื่อครบ TRACE_RETENTION_MONTHS: text_redacted, payload -> NULL (ทีละก้อน แตะเฉพาะแถวที่ยังมีค่า)

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 7: PgMessagesRepo ตาม MessagesRepo + R2-A: retention รูป / anonymize)
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.pipeline.schemas import (
    AttachmentRef,
    ChatTurn,
    OutboundMessage,
    OutboundReservation,
    OutboundStatus,
)
from app.repositories.db import jsonb


def inbound_dedupe_key(raw_event_id: int) -> str:
    """dedupe_key ของข้อความขาเข้า (ตามหมายเหตุใน migration)"""
    return f"in:{raw_event_id}"


class PgMessagesRepo:
    """MessagesRepo บน Postgres — กันซ้ำด้วย UNIQUE dedupe_key"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @staticmethod
    async def _existing(conn: AsyncConnection[Any], dedupe_key: str) -> dict[str, Any]:
        """แถวเดิมที่ชน dedupe_key (ON CONFLICT DO NOTHING รอ transaction ที่แทรกก่อน commit แล้วจึงอ่านเห็น)"""
        cur = await conn.execute(
            "SELECT id, status FROM messages WHERE dedupe_key = %s", (dedupe_key,)
        )
        row = await cur.fetchone()
        if row is None:
            # แถวที่ชนถูกลบไประหว่างทาง (แทบไม่เกิด) — ให้ผู้เรียก retry ทั้งงาน
            raise RuntimeError("dedupe_key ชนแต่หาแถวเดิมไม่พบ")
        return row

    async def add_inbound(
        self,
        *,
        conversation_id: int,
        raw_event_id: int,
        message_type: str,
        text_redacted: str | None,
    ) -> int:
        key = inbound_dedupe_key(raw_event_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO messages
                    (conversation_id, raw_event_id, direction, message_type, text_redacted, dedupe_key)
                VALUES (%s, %s, 'in', %s, %s, %s)
                ON CONFLICT (dedupe_key) DO NOTHING
                RETURNING id
                """,
                (conversation_id, raw_event_id, message_type, text_redacted, key),
            )
            row = await cur.fetchone()
            if row is None:
                row = await self._existing(conn, key)
        return row["id"]

    async def recent_texts_redacted(
        self,
        conversation_id: int,
        limit: int = 4,
        *,
        exclude_raw_event_id: int | None = None,
        exclude_types: Collection[str] = (),
    ) -> list[ChatTurn]:
        if limit <= 0:
            return []
        async with self._pool.connection() as conn:
            # IS NOT DISTINCT FROM: raw_event_id เป็น NULL (raw ถูกลบตาม retention) ต้องไม่หลุดเงื่อนไข
            cur = await conn.execute(
                """
                SELECT direction, text_redacted FROM (
                    SELECT id, created_at, direction, text_redacted
                    FROM messages
                    WHERE conversation_id = %(conv)s
                      AND text_redacted IS NOT NULL AND text_redacted <> ''
                      AND (direction = 'in' OR status = 'sent')
                      AND NOT (direction = 'out' AND message_type = ANY(%(skip)s::text[]))
                      AND NOT (
                          direction = 'in'
                          AND %(ex)s::bigint IS NOT NULL
                          AND raw_event_id IS NOT DISTINCT FROM %(ex)s::bigint
                      )
                    ORDER BY created_at DESC, id DESC
                    LIMIT %(limit)s
                ) AS recent
                ORDER BY created_at, id
                """,
                {
                    "conv": conversation_id,
                    "ex": exclude_raw_event_id,
                    "skip": list(exclude_types),
                    "limit": limit,
                },
            )
            rows = await cur.fetchall()
        return [ChatTurn.model_validate(r) for r in rows]

    async def count_inbound_since(self, conversation_id: int, since: datetime) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT count(*) AS n FROM messages
                WHERE conversation_id = %s AND direction = 'in' AND created_at >= %s
                """,
                (conversation_id, since),
            )
            row = await cur.fetchone()
        return 0 if row is None else int(row["n"])

    async def reserve_outbound(
        self,
        *,
        conversation_id: int,
        raw_event_id: int | None,
        dedupe_key: str,
        outbound: OutboundMessage,
        text_redacted: str | None = None,
    ) -> OutboundReservation:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO messages
                    (conversation_id, raw_event_id, direction, message_type, text_redacted,
                     payload, status, dedupe_key)
                VALUES (%s, %s, 'out', %s, %s, %s, 'pending', %s)
                ON CONFLICT (dedupe_key) DO NOTHING
                RETURNING id
                """,
                (
                    conversation_id,
                    raw_event_id,
                    outbound.purpose,
                    text_redacted,
                    jsonb(list(outbound.messages)),
                    dedupe_key,
                ),
            )
            row = await cur.fetchone()
            if row is not None:
                return OutboundReservation(message_id=row["id"], already_sent=False)
            existing = await self._existing(conn, dedupe_key)
        return OutboundReservation(
            message_id=existing["id"], already_sent=existing["status"] == OutboundStatus.SENT.value
        )

    async def mark_outbound(
        self, message_id: int, status: OutboundStatus, *, reason: str | None = None
    ) -> None:
        status = OutboundStatus(status)
        if status is OutboundStatus.SENT:
            sql = "UPDATE messages SET status = %s, sent_at = now() WHERE id = %s AND direction = 'out'"
            params: tuple[Any, ...] = (status.value, message_id)
        elif status in (OutboundStatus.DROPPED, OutboundStatus.FAILED):
            sql = "UPDATE messages SET status = %s, drop_reason = %s WHERE id = %s AND direction = 'out'"
            params = (status.value, reason, message_id)
        else:
            sql = "UPDATE messages SET status = %s WHERE id = %s AND direction = 'out'"
            params = (status.value, message_id)
        async with self._pool.connection() as conn:
            await conn.execute(sql, params)

    async def add_attachment(
        self, *, message_id: int, storage_key: str, content_type: str, size_bytes: int
    ) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO attachments (message_id, storage_key, content_type, size_bytes)
                VALUES (%s, %s, %s, %s)
                -- retry event เดิม (storage_key เดิม) -> คืน id แถวเดิม ไม่ error
                ON CONFLICT (storage_key) DO UPDATE SET storage_key = EXCLUDED.storage_key
                RETURNING id
                """,
                (message_id, storage_key, content_type, size_bytes),
            )
            row = await cur.fetchone()
        assert row is not None
        return row["id"]

    async def list_expired_attachments(self, before: datetime, limit: int = 500) -> list[AttachmentRef]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, storage_key FROM attachments
                WHERE created_at < %s AND deleted_at IS NULL
                ORDER BY created_at, id
                LIMIT %s
                """,
                (before, limit),
            )
            rows = await cur.fetchall()
        return [AttachmentRef.model_validate(r) for r in rows]

    async def mark_attachment_deleted(self, attachment_id: int) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE attachments SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL",
                (attachment_id,),
            )

    async def anonymize_older_than(self, before: datetime, limit: int = 5000) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE messages SET text_redacted = NULL, payload = NULL
                WHERE id IN (
                    SELECT id FROM messages
                    WHERE created_at < %s
                      AND (text_redacted IS NOT NULL OR payload IS NOT NULL)
                    ORDER BY created_at, id
                    LIMIT %s
                )
                """,
                (before, limit),
            )
            return cur.rowcount


__all__ = ["PgMessagesRepo", "inbound_dedupe_key"]
