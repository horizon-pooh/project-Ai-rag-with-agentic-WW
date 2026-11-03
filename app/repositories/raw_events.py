"""app/repositories/raw_events.py

ตาราง line_raw_events (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - insert แบบกันซ้ำด้วย UNIQUE webhook_event_id (ตัด replyToken ออก)
 - process_status: pending -> processing -> done / failed
 - ดึง event ค้างของ user ตามลำดับเวลา
 - หา pending ค้างนาน (ให้ cron requeue_pending)
 - ลบเกิน 90 วัน (retention)

แนวคิดจาก: src/pipeline/dedupe.js, src/store/redisStore.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 7: PgRawEventsRepo ตาม RawEventsRepo ใน app/pipeline/ports.py + delete_older_than)
"""

from __future__ import annotations

from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from app.pipeline.schemas import JsonDict, ProcessStatus, RawEvent
from app.repositories.db import columns, jsonb

# ตรงกับฟิลด์ของ RawEvent ทุกตัว (tests/repositories/test_sql_static.py ตรวจ)
RAW_EVENT_COLUMNS = (
    "id",
    "webhook_event_id",
    "line_user_id",
    "event_type",
    "payload",
    "occurred_at",
    "received_at",
    "process_status",
    "claimed_at",
    "processed_at",
    "attempts",
    "error",
)
_COLS = columns(*RAW_EVENT_COLUMNS)

# retention ลบทีละก้อน กัน transaction ยาว
DEFAULT_DELETE_BATCH = 5000


class PgRawEventsRepo:
    """RawEventsRepo บน Postgres — UPDATE แบบมีเงื่อนไข: ได้ 0 แถว -> None/ไม่ทำอะไร"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def insert(
        self,
        *,
        webhook_event_id: str,
        line_user_id: str | None,
        event_type: str,
        payload: JsonDict,
        occurred_at: datetime,
    ) -> int | None:
        if "replyToken" in payload:
            # ตรวจก่อนถึง DB (CHECK line_raw_events_no_reply_token ก็ปฏิเสธเหมือนกัน)
            raise ValueError("payload ต้องตัด replyToken ออก (CHECK line_raw_events_no_reply_token)")
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO line_raw_events
                    (webhook_event_id, line_user_id, event_type, payload, occurred_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (webhook_event_id) DO NOTHING
                RETURNING id
                """,
                (webhook_event_id, line_user_id, event_type, jsonb(payload), occurred_at),
            )
            row = await cur.fetchone()
        return None if row is None else row["id"]

    async def get(self, raw_event_id: int) -> RawEvent | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM line_raw_events WHERE id = %s", (raw_event_id,)
            )
            row = await cur.fetchone()
        return None if row is None else RawEvent.model_validate(row)

    async def claim(self, raw_event_id: int) -> RawEvent | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""
                UPDATE line_raw_events
                SET process_status = 'processing', claimed_at = now(), attempts = attempts + 1
                WHERE id = %s AND process_status = 'pending'
                RETURNING {_COLS}
                """,
                (raw_event_id,),
            )
            row = await cur.fetchone()
        return None if row is None else RawEvent.model_validate(row)

    async def mark_done(
        self, raw_event_id: int, status: ProcessStatus = ProcessStatus.DONE
    ) -> None:
        if status not in (ProcessStatus.DONE, ProcessStatus.SKIPPED):
            raise ValueError("mark_done รับแค่ DONE หรือ SKIPPED")
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE line_raw_events
                SET process_status = %s, processed_at = now(), error = NULL
                WHERE id = %s
                """,
                # ส่ง .value เสมอ (กัน enum ถูกแปลงเป็นชื่อ เช่น 'DONE')
                (ProcessStatus(status).value, raw_event_id),
            )

    async def mark_failed(self, raw_event_id: int, error: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE line_raw_events
                SET process_status = 'failed', processed_at = now(), error = %s
                WHERE id = %s
                """,
                (error, raw_event_id),
            )

    async def release(self, raw_event_id: int, error: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE line_raw_events
                SET process_status = 'pending', error = %s
                WHERE id = %s AND process_status = 'processing'
                """,
                (error, raw_event_id),
            )

    async def list_pending_ids_for_user(self, line_user_id: str) -> list[int]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id FROM line_raw_events
                WHERE line_user_id = %s AND process_status = 'pending'
                ORDER BY occurred_at, id
                """,
                (line_user_id,),
            )
            rows = await cur.fetchall()
        return [r["id"] for r in rows]

    async def list_stale(self, received_before: datetime, limit: int = 100) -> list[int]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id FROM line_raw_events
                WHERE process_status = 'pending' AND received_at < %s
                ORDER BY received_at, id
                LIMIT %s
                """,
                (received_before, limit),
            )
            rows = await cur.fetchall()
        return [r["id"] for r in rows]

    async def release_stuck(self, claimed_before: datetime, limit: int = 100) -> list[int]:
        async with self._pool.connection() as conn:
            # ล็อกแถวที่จะปล่อยก่อน (SKIP LOCKED: แถวที่มีคนกำลังแก้ เช่น mark_done ข้ามไป)
            # แล้ว UPDATE ซ้ำเงื่อนไข processing; คืน id เรียง claimed_at เก่าสุดก่อน
            cur = await conn.execute(
                """
                WITH stuck AS (
                    SELECT id FROM line_raw_events
                    WHERE process_status = 'processing' AND claimed_at < %s
                    ORDER BY claimed_at, id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                ), released AS (
                    UPDATE line_raw_events AS r
                    SET process_status = 'pending'
                    FROM stuck
                    WHERE r.id = stuck.id AND r.process_status = 'processing'
                    RETURNING r.id, r.claimed_at
                )
                SELECT id FROM released ORDER BY claimed_at, id
                """,
                (claimed_before, limit),
            )
            rows = await cur.fetchall()
        return [r["id"] for r in rows]

    # --- ไม่อยู่ในพอร์ต: งาน retention (cron) ---

    async def delete_older_than(self, cutoff: datetime, limit: int = DEFAULT_DELETE_BATCH) -> int:
        """ลบแถวที่ received_at < cutoff ไม่เกิน limit แถว คืนจำนวนที่ลบ (ผู้เรียกวนจนได้ < limit)

        ลบทุกสถานะ (เกินอายุเก็บแล้วต้องลบแม้ค้าง); messages/ai_runs/ai_steps ที่อ้างถึงได้ raw_event_id=NULL (FK SET NULL)
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                DELETE FROM line_raw_events
                WHERE id IN (
                    SELECT id FROM line_raw_events
                    WHERE received_at < %s
                    ORDER BY received_at, id
                    LIMIT %s
                )
                """,
                (cutoff, limit),
            )
            return cur.rowcount


__all__ = ["RAW_EVENT_COLUMNS", "PgRawEventsRepo"]
