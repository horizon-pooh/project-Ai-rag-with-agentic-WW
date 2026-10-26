"""tests/repositories/test_pg_health.py

queue_health กับ Postgres จริง — ข้ามทั้งไฟล์ถ้าไม่ได้ตั้ง TEST_DATABASE_URL (fixture db_conninfo ใน conftest.py)

รายละเอียด:
 - ว่างเปล่า -> workers=0, อายุเป็น None
 - worker heartbeat เก่า + raw pending เก่า -> อายุตรงตามที่ตั้ง (คลาดได้ไม่กี่วินาที)
 - raw ที่ไม่ใช่ pending ไม่นับ

แนวคิดจาก: ใหม่ (D-012)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from app.repositories.health import queue_health
from tests.repositories.conftest import Db


async def test_empty(pool: AsyncConnectionPool) -> None:
    h = await queue_health(pool)
    assert (h.workers, h.heartbeat_age_s, h.oldest_pending_age_s) == (0, None, None)


async def test_ages(pool: AsyncConnectionPool, db: Db) -> None:
    await db.fetch(
        "INSERT INTO procrastinate_workers (last_heartbeat)"
        " VALUES (now() - interval '300 seconds'), (now() - interval '200 seconds')"
    )
    for i, (status, age) in enumerate([("pending", 900), ("pending", 100), ("done", 5000)]):
        await db.fetch(
            "INSERT INTO line_raw_events (webhook_event_id, event_type, payload, occurred_at,"
            " received_at, process_status)"
            " VALUES (%s, 'message', '{}'::jsonb, now(), now() - make_interval(secs => %s), %s)",
            (f"e{i}", age, status),
        )
    h = await queue_health(pool)
    assert h.workers == 2
    assert h.heartbeat_age_s is not None and 199 <= h.heartbeat_age_s < 210
    assert h.oldest_pending_age_s is not None and 899 <= h.oldest_pending_age_s < 910
