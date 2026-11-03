"""app/repositories/users.py

ตาราง line_users และ line_consents (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - หา/สร้าง user จาก LINE user id
 - consent: อ่าน/บันทึกการยินยอม (PDPA)
 - get_line_user_id: หา line_user_id จาก user id (ย้ายมาจาก repositories/lookups.py เดิม) — ลบแล้ว -> None
 - delete_user (PDPA): transaction เดียว เก็บ storage_key ของ attachments -> ลบ line_raw_events ตาม line_user_id
   (ไม่มี FK) -> ลบ line_users (cascade ที่เหลือ); ไฟล์ใน MinIO ผู้เรียก (app/pipeline/pdpa.py) ลบแยก

แนวคิดจาก: src/store/redisStore.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 7 + R2-A: get_line_user_id, delete_user) — ยังไม่มีเมธอดบันทึก consent (ไม่อยู่ในพอร์ต)
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from app.pipeline.schemas import User
from app.repositories.db import columns

USER_COLUMNS = ("id", "line_user_id", "display_name", "created_at", "last_seen_at", "deleted_at")
_COLS = columns(*USER_COLUMNS)


class PgUsersRepo:
    """UsersRepo บน Postgres"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get_or_create(self, line_user_id: str, display_name: str | None = None) -> User:
        # คำสั่งเดียวทั้งหาและสร้าง (ไม่มี race): ชนกัน -> อัปเดต last_seen_at; display_name None -> คงค่าเดิม
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""
                INSERT INTO line_users (line_user_id, display_name)
                VALUES (%s, %s)
                ON CONFLICT (line_user_id) DO UPDATE
                SET last_seen_at = now(),
                    display_name = COALESCE(EXCLUDED.display_name, line_users.display_name)
                RETURNING {_COLS}
                """,
                (line_user_id, display_name),
            )
            row = await cur.fetchone()
        assert row is not None  # INSERT ... ON CONFLICT DO UPDATE คืนแถวเสมอ
        return User.model_validate(row)

    async def has_consent(self, user_id: int, consent_type: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT granted FROM line_consents
                WHERE user_id = %s AND consent_type = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user_id, consent_type),
            )
            row = await cur.fetchone()
        return row is not None and bool(row["granted"])

    async def get_line_user_id(self, user_id: int) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT line_user_id FROM line_users WHERE id = %s AND deleted_at IS NULL",
                (user_id,),
            )
            row = await cur.fetchone()
        return None if row is None else row["line_user_id"]

    async def delete_user(self, line_user_id: str) -> list[str] | None:
        async with self._pool.connection() as conn, conn.transaction():
            # ล็อกแถว user กัน get_or_create/บันทึกข้อความแทรกระหว่างเก็บ key กับลบ
            cur = await conn.execute(
                "SELECT id FROM line_users WHERE line_user_id = %s FOR UPDATE", (line_user_id,)
            )
            user = await cur.fetchone()
            keys: list[str] = []
            if user is not None:
                cur = await conn.execute(
                    """
                    SELECT a.storage_key
                    FROM attachments AS a
                    JOIN messages AS m ON m.id = a.message_id
                    JOIN conversations AS c ON c.id = m.conversation_id
                    WHERE c.user_id = %s AND a.deleted_at IS NULL
                    ORDER BY a.id
                    """,
                    (user["id"],),
                )
                keys = [r["storage_key"] for r in await cur.fetchall()]
            # line_raw_events ไม่มี FK ไป line_users -> ลบตาม line_user_id เสมอ (แม้ไม่มีแถว user)
            await conn.execute("DELETE FROM line_raw_events WHERE line_user_id = %s", (line_user_id,))
            if user is None:
                return None
            # cascade: consents, conversations -> state, messages -> attachments, ai_runs -> ai_steps, feedback, close_tokens
            await conn.execute("DELETE FROM line_users WHERE id = %s", (user["id"],))
        return keys


__all__ = ["USER_COLUMNS", "PgUsersRepo"]
