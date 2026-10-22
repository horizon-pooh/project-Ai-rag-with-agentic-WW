"""app/repositories/health.py

ตรวจว่าต่อ Postgres ได้ (ให้ /health/ready) — SQL อยู่ในชั้น repositories ตามกติกา

แนวคิดจาก: src/routes/health.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8)
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool


async def ping(pool: AsyncConnectionPool) -> bool:
    """SELECT 1 ผ่าน pool; ได้ผล -> True (error ปล่อยให้ผู้เรียกจับ)"""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT 1 AS ok")
        row = await cur.fetchone()
    return row is not None and row["ok"] == 1


__all__ = ["ping"]
