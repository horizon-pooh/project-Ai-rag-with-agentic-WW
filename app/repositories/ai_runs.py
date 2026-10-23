"""app/repositories/ai_runs.py

ตาราง ai_runs และ ai_steps (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - สร้าง ai_runs ต่อหนึ่งข้อความ (ผูก raw_event_id)
 - ai_steps (orchestrator เป็นคนเรียก): input redact (รูปเก็บแค่ id), output + raw ตัดแล้ว, latency_ms, attempts, error_class, cost (ถ้ามี), raw_event_id, ai_run_id
 - ใช้ทำเวลาตอบ p95 และต้นทุนต่อบทสนทนา
 - anonymize เมื่อครบ TRACE_RETENTION_MONTHS: input, output, state_before, state_after -> NULL
   (ทีละก้อน แตะเฉพาะแถวที่ยังมีค่า; latency_ms / attempts / error_class / cost เก็บไว้ทำสถิติ)

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 7: PgAiRunsRepo ตาม AiRunsRepo + R2-A: anonymize)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from psycopg_pool import AsyncConnectionPool

from app.pipeline.schemas import AiStepRecord
from app.repositories.db import jsonb

# คอลัมน์ของ ai_steps ที่มาจาก AiStepRecord (ตรงกับฟิลด์ทุกตัว — test_sql_static ตรวจ)
AI_STEP_COLUMNS = (
    "ai_run_id",
    "raw_event_id",
    "seq",
    "step",
    "input",
    "output",
    "state_before",
    "state_after",
    "latency_ms",
    "attempts",
    "error_class",
    "cost",
)
_JSON_STEP_COLUMNS = frozenset({"input", "output", "state_before", "state_after"})


class PgAiRunsRepo:
    """AiRunsRepo บน Postgres"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create_run(self, *, raw_event_id: int, conversation_id: int) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO ai_runs (raw_event_id, conversation_id)
                VALUES (%s, %s)
                ON CONFLICT (raw_event_id) DO NOTHING
                RETURNING id
                """,
                (raw_event_id, conversation_id),
            )
            row = await cur.fetchone()
            if row is None:
                # retry ของ event เดิม -> แถวเดิม
                cur = await conn.execute(
                    "SELECT id FROM ai_runs WHERE raw_event_id = %s", (raw_event_id,)
                )
                row = await cur.fetchone()
                if row is None:
                    raise RuntimeError("ai_runs ชน raw_event_id แต่หาแถวเดิมไม่พบ")
        return row["id"]

    async def add_step(self, step: AiStepRecord) -> None:
        values = [
            jsonb(getattr(step, c)) if c in _JSON_STEP_COLUMNS else getattr(step, c)
            for c in AI_STEP_COLUMNS
        ]
        placeholders = ", ".join(["%s"] * len(AI_STEP_COLUMNS))
        async with self._pool.connection() as conn:
            await conn.execute(
                f"INSERT INTO ai_steps ({', '.join(AI_STEP_COLUMNS)}) VALUES ({placeholders})",
                values,
            )

    async def finish_run(
        self,
        ai_run_id: int,
        *,
        route: str,
        reason: str | None = None,
        confidence: Decimal | None = None,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE ai_runs
                SET finished_at = now(), route = %s, reason = %s, confidence = %s
                WHERE id = %s
                """,
                (route, reason, confidence, ai_run_id),
            )

    async def anonymize_steps_older_than(self, before: datetime, limit: int = 5000) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE ai_steps SET input = NULL, output = NULL, state_before = NULL, state_after = NULL
                WHERE id IN (
                    SELECT id FROM ai_steps
                    WHERE created_at < %s
                      AND (input IS NOT NULL OR output IS NOT NULL
                           OR state_before IS NOT NULL OR state_after IS NOT NULL)
                    ORDER BY created_at, id
                    LIMIT %s
                )
                """,
                (before, limit),
            )
            return cur.rowcount


__all__ = ["AI_STEP_COLUMNS", "PgAiRunsRepo"]
