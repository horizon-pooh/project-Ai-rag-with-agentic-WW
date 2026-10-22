"""app/repositories/feedback.py

ตาราง feedback (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - บันทึกถูกใจ / ไม่ถูกใจ และการกดการ์ด/ปุ่มดูสินค้า (สัญญาณแทนความสำเร็จ)
 - ป้ายจากการสุ่มตรวจรายสัปดาห์ (สำเร็จ / ไม่สำเร็จ / ไม่แน่ใจ)

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 7: PgFeedbackRepo ตาม FeedbackRepo)
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from app.pipeline.schemas import FeedbackKind


class PgFeedbackRepo:
    """FeedbackRepo บน Postgres"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def add(
        self,
        *,
        conversation_id: int,
        kind: FeedbackKind,
        value: str | None = None,
        ai_run_id: int | None = None,
    ) -> int:
        # ai_run_id มาจาก postback (ลูกค้าแก้ได้): เก็บเฉพาะเมื่อเป็น run ของ conversation นี้ ไม่งั้น NULL
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO feedback (conversation_id, ai_run_id, kind, value)
                VALUES (
                    %(conv)s,
                    (SELECT id FROM ai_runs WHERE id = %(run)s::bigint AND conversation_id = %(conv)s),
                    %(kind)s,
                    %(value)s
                )
                RETURNING id
                """,
                {
                    "conv": conversation_id,
                    # นอกช่วง bigint -> ไม่มีทางเป็น run จริง (กัน error ตอน cast)
                    "run": ai_run_id if ai_run_id is not None and 0 < ai_run_id < 2**63 else None,
                    "kind": FeedbackKind(kind).value,
                    "value": value,
                },
            )
            row = await cur.fetchone()
        assert row is not None
        return row["id"]


__all__ = ["PgFeedbackRepo"]
