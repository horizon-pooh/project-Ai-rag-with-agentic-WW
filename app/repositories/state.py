"""app/repositories/state.py

ตาราง conversation_state (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - อ่านเฉพาะแถวที่ยังไม่หมดอายุ (expires_at > now())
 - เขียน/ต่ออายุ STATE_TTL_HOURS (2 ชม.)
 - ล้างเมื่อ end_handoff สำเร็จ
 - ไม่ต่ออายุระหว่าง human

แนวคิดจาก: src/store/redisStore.js (แบบเก่า: session 24 ชม. ล้าสมัย)
สถานะ: มีโค้ดแล้ว (งาน 7: PgStateRepo ตาม StateRepo) — ตามสัญญาใน ports.py get คืนแถวแม้หมดอายุ
  (prepare ตัดสินด้วย StateRecord.is_expired); ล้างตอน end_handoff อยู่ใน conversations.py (transaction เดียวกัน)
"""

from __future__ import annotations

from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from app.pipeline.schemas import Counters, JsonDict, StateRecord
from app.repositories.db import columns, jsonb

STATE_COLUMNS = ("conversation_id", "state", "counters", "expires_at")
_COLS = columns(*STATE_COLUMNS)


class PgStateRepo:
    """StateRepo บน Postgres"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get(self, conversation_id: int) -> StateRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM conversation_state WHERE conversation_id = %s",
                (conversation_id,),
            )
            row = await cur.fetchone()
        return None if row is None else StateRecord.model_validate(row)

    async def save(
        self,
        conversation_id: int,
        *,
        state: JsonDict | None,
        counters: Counters,
        expires_at: datetime,
    ) -> None:
        # counters เป็น JSON ล้วน (Decimal ของราคา -> string) อ่านกลับด้วย Counters.model_validate
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_state (conversation_id, state, counters, expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (conversation_id) DO UPDATE
                SET state = EXCLUDED.state,
                    counters = EXCLUDED.counters,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = now()
                """,
                (
                    conversation_id,
                    jsonb(state),
                    jsonb(counters.model_dump(mode="json")),
                    expires_at,
                ),
            )

    async def clear(self, conversation_id: int) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM conversation_state WHERE conversation_id = %s", (conversation_id,)
            )


__all__ = ["STATE_COLUMNS", "PgStateRepo"]
