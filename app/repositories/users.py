"""app/repositories/users.py

ตาราง line_users และ line_consents (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - หา/สร้าง user จาก LINE user id
 - consent: อ่าน/บันทึกการยินยอม (PDPA)
 - ลบตาม PDPA ทำผ่าน line_users ที่เดียว (cascade) ไฟล์ใน MinIO ต้องลบแยก

แนวคิดจาก: src/store/redisStore.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 7: PgUsersRepo ตาม UsersRepo — get_or_create, has_consent; ยังไม่มีเมธอดบันทึก consent/ลบ user เพราะไม่อยู่ในพอร์ต)
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


__all__ = ["USER_COLUMNS", "PgUsersRepo"]
