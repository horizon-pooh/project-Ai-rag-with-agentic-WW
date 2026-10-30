"""app/repositories/db.py

เปิด/ปิด connection pool ของ Postgres (psycopg 3 async + psycopg_pool) ให้ repository ทุกตัวใช้ร่วมกัน

รายละเอียด:
 - open_pool(database_url): AsyncConnectionPool แบบ open=False แล้ว await open(wait=True) -> ต่อ DB ไม่ได้ = หยุดตอนเริ่ม (fail fast)
 - ทุก connection ใช้ row_factory=dict_row (repository แปลง dict -> model ใน app/pipeline/schemas.py เอง)
 - ไม่ใช่ autocommit: `async with pool.connection()` commit ตอนออกจาก block (error -> rollback)
   หนึ่งเมธอดของ repository = หนึ่ง transaction สั้น ๆ ห้ามถือ connection ค้างระหว่างรอ AI/LINE
 - Windows: psycopg แบบ async ใช้ ProactorEventLoop (ค่าตั้งต้นของ Windows) ไม่ได้ ต้องเป็น SelectorEventLoop
   (production รันใน Linux container จึงไม่มีปัญหา; เทสต์บน Windows ตั้งใน tests/repositories/conftest.py)

แนวคิดจาก: ใหม่ (docs/PLAN.md: DB driver = psycopg 3 async + psycopg_pool)
สถานะ: มีโค้ดแล้ว (งาน 7)
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

# (เสนอ) ขนาด pool: api + worker แต่ละ process ถือ pool ของตัวเอง ปรับตามจำนวน worker/AI_CONCURRENCY
DEFAULT_MIN_SIZE = 1
DEFAULT_MAX_SIZE = 10
# รอ connection แรกตอนเริ่มนานสุดเท่านี้ (วินาที) แล้วยก error
OPEN_TIMEOUT_S = 30.0


async def open_pool(
    database_url: str | SecretStr,
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    max_size: int = DEFAULT_MAX_SIZE,
    timeout_s: float = OPEN_TIMEOUT_S,
) -> AsyncConnectionPool:
    """สร้างและเปิด pool (รอจนได้ connection ครบ min_size) — database_url อาจมีรหัสผ่าน ห้ามลง log"""
    conninfo = (
        database_url.get_secret_value() if isinstance(database_url, SecretStr) else database_url
    )
    pool = AsyncConnectionPool(
        conninfo,
        min_size=min_size,
        max_size=max_size,
        open=False,
        kwargs={"row_factory": dict_row},
        # ตรวจ connection ก่อนยืมทุกครั้ง (DB restart แล้ว connection เก่าตาย)
        check=AsyncConnectionPool.check_connection,
        name="line_prod",
    )
    try:
        await pool.open(wait=True, timeout=timeout_s)
    except BaseException:
        await pool.close()
        raise
    return pool


async def close_pool(pool: AsyncConnectionPool) -> None:
    """ปิด pool (รอ connection ที่ยืมไปคืนก่อน) — เรียกตอน shutdown ของ api/worker"""
    await pool.close()


def jsonb(value: Any) -> Jsonb | None:
    """ค่า Python -> พารามิเตอร์ jsonb; None -> SQL NULL (ไม่ใช่ jsonb 'null')"""
    return None if value is None else Jsonb(value)


def columns(*names: str) -> str:
    """รายชื่อคอลัมน์สำหรับ SELECT/RETURNING (เขียนชื่อเองทุกครั้ง ไม่ใช้ * เพราะ model เป็น extra='forbid')"""
    return ", ".join(names)


__all__ = ["close_pool", "columns", "jsonb", "open_pool"]
