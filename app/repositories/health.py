"""app/repositories/health.py

ตรวจว่าต่อ Postgres ได้ (ให้ /health/ready) และสุขภาพของคิว (ให้ watchdog) — SQL อยู่ในชั้น repositories ตามกติกา

รายละเอียด:
 - ping: SELECT 1
 - queue_health: จำนวน worker ที่ลงทะเบียนใน procrastinate_workers, อายุ heartbeat ล่าสุด, อายุ raw event pending ที่เก่าสุด
   (worker ปิดปกติ = ลบแถวตัวเอง; worker ตายกะทันหัน = แถวค้างและ heartbeat เก่าลงเรื่อยๆ)
   อ่านอย่างเดียว ไม่เขียนตาราง procrastinate_*

แนวคิดจาก: src/routes/health.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8 + queue_health ของ watchdog — D-012)
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True, kw_only=True)
class QueueHealth:
    """ภาพรวมคิวหนึ่งครั้ง — อายุเป็นวินาที (None = ไม่มีแถว)"""

    workers: int
    heartbeat_age_s: float | None
    oldest_pending_age_s: float | None


async def ping(pool: AsyncConnectionPool) -> bool:
    """SELECT 1 ผ่าน pool; ได้ผล -> True (error ปล่อยให้ผู้เรียกจับ)"""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT 1 AS ok")
        row = await cur.fetchone()
    return row is not None and row["ok"] == 1


async def queue_health(pool: AsyncConnectionPool) -> QueueHealth:
    """อ่านสุขภาพคิวครั้งเดียว (error ปล่อยให้ผู้เรียกจับ = ต่อ DB ไม่ได้)"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT
                (SELECT count(*) FROM procrastinate_workers) AS workers,
                (SELECT EXTRACT(EPOCH FROM now() - max(last_heartbeat))
                   FROM procrastinate_workers) AS heartbeat_age_s,
                (SELECT EXTRACT(EPOCH FROM now() - min(received_at))
                   FROM line_raw_events WHERE process_status = 'pending') AS oldest_pending_age_s
            """
        )
        row = await cur.fetchone()
    assert row is not None  # SELECT ที่ไม่มี FROM คืนหนึ่งแถวเสมอ

    def _age(v: object) -> float | None:
        # EXTRACT คืน numeric (Decimal) — แปลงเป็น float; ติดลบได้ถ้านาฬิกาเหลื่อม -> ปัดเป็น 0
        return None if v is None else max(float(v), 0.0)  # type: ignore[arg-type]

    return QueueHealth(
        workers=int(row["workers"]),
        heartbeat_age_s=_age(row["heartbeat_age_s"]),
        oldest_pending_age_s=_age(row["oldest_pending_age_s"]),
    )


__all__ = ["QueueHealth", "ping", "queue_health"]
