"""app/repositories/reply_tokens.py

ตาราง reply_tokens (implement ports.ReplyTokens) — แทน Redis เดิม (docs/decisions.md D-010)

รายละเอียด:
 - put: upsert แถวของ raw_event_id, expires_at = now() + REPLY_TOKEN_TTL_S (นาฬิกาของ DB) — webhook เรียกหลัง insert raw
 - take: DELETE ... WHERE raw_event_id = %s AND expires_at > now() RETURNING token — ใช้ได้ครั้งเดียว
   None = ไม่มี / หมดอายุ / ใช้ไปแล้ว -> sendGuard ใช้ push
 - delete_expired: ให้ cron retention ลบแถวที่หมดอายุแล้วแต่ไม่ถูก take (ไม่อยู่ในพอร์ต)
 - replyToken เป็นความลับอายุสั้น: ห้ามลง log; ไม่อยู่ใน line_raw_events.payload (CHECK ใน migration แรก)
 - ลบ raw event (retention / PDPA) -> แถวที่นี่ลบตาม (FK ON DELETE CASCADE)

แนวคิดจาก: app/services/redis_store.py เดิม (SET EX + GETDEL)
สถานะ: มีโค้ดแล้ว (D-010)
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

# retention ลบทีละก้อน กัน transaction ยาว
DEFAULT_DELETE_BATCH = 5000


class PgReplyTokens:
    """ReplyTokens บน Postgres"""

    def __init__(self, pool: AsyncConnectionPool, ttl_s: int) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s ต้องมากกว่า 0")
        self._pool = pool
        self._ttl_s = ttl_s

    async def put(self, raw_event_id: int, reply_token: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO reply_tokens (raw_event_id, token, expires_at)
                VALUES (%s, %s, now() + make_interval(secs => %s))
                ON CONFLICT (raw_event_id)
                    DO UPDATE SET token = EXCLUDED.token, expires_at = EXCLUDED.expires_at
                """,
                (raw_event_id, reply_token, self._ttl_s),
            )

    async def take(self, raw_event_id: int) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                DELETE FROM reply_tokens
                WHERE raw_event_id = %s AND expires_at > now()
                RETURNING token
                """,
                (raw_event_id,),
            )
            row = await cur.fetchone()
        return None if row is None else (row["token"] or None)

    async def delete_expired(self, limit: int = DEFAULT_DELETE_BATCH) -> int:
        """ลบแถวที่หมดอายุแล้ว (สูงสุด limit แถว) คืนจำนวนที่ลบ"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                DELETE FROM reply_tokens
                WHERE raw_event_id IN (
                    SELECT raw_event_id FROM reply_tokens
                    WHERE expires_at <= now()
                    ORDER BY expires_at
                    LIMIT %s
                )
                """,
                (limit,),
            )
            return cur.rowcount


__all__ = ["DEFAULT_DELETE_BATCH", "PgReplyTokens"]
