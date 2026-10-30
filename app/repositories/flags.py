"""app/repositories/flags.py

ตาราง ops_flags (ที่เดียวที่เขียน SQL) — ค่าตั้งที่เปลี่ยนได้ตอนรัน เช่น kill_switch

รายละเอียด:
 - get_flag: อ่านค่าของ key (ไม่มี -> None) services/kill_switch.py อ่านทุกครั้งที่ถาม
 - set_flag: UPSERT ค่า + updated_at=now() (เปิด/ปิดไม่ต้อง deploy)

แนวคิดจาก: ใหม่ (docs/PLAN.md: kill switch = ops_flags OR env KILL_SWITCH)
สถานะ: มีโค้ดแล้ว (งาน 7: PgFlagsRepo ตาม FlagsRepo)
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool


class PgFlagsRepo:
    """FlagsRepo บน Postgres"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get_flag(self, key: str) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT value FROM ops_flags WHERE key = %s", (key,))
            row = await cur.fetchone()
        return None if row is None else row["value"]

    async def set_flag(self, key: str, value: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO ops_flags (key, value) VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                (key, value),
            )


__all__ = ["PgFlagsRepo"]
