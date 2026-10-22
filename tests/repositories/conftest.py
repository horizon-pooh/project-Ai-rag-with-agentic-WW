"""tests/repositories/conftest.py

fixture ของ integration test ที่ต่อ Postgres จริง (app/repositories/*)

รายละเอียด:
 - ข้ามทุกเทสต์ที่ใช้ DB ถ้าไม่ได้ตั้ง env TEST_DATABASE_URL (เช่น postgresql://user:pass@localhost:5432/test)
   test_sql_static.py ไม่ใช้ DB จึงรันเสมอ
 - ต่อหนึ่ง session: CREATE SCHEMA ชื่อสุ่ม -> รันส่วน `-- migrate:up` ของ migration ใน schema นั้น
   -> ทุก connection ของ pool ตั้ง search_path ไปที่ schema นั้น -> จบ session DROP SCHEMA ... CASCADE
 - ต่อหนึ่งเทสต์: pool ใหม่ + TRUNCATE ทุกตาราง (RESTART IDENTITY) ให้เริ่มจากว่าง
 - Windows: psycopg async ใช้ ProactorEventLoop ไม่ได้ -> ใช้ SelectorEventLoop เฉพาะเทสต์ในโฟลเดอร์นี้
   (ผ่าน hook pytest_asyncio_loop_factories ของ pytest-asyncio 1.x ซึ่งมีผลกับ item ใต้ conftest นี้เท่านั้น
   แทนการตั้ง WindowsSelectorEventLoopPolicy ทั้ง process ที่ deprecated และกระทบเทสต์อื่น)
 - SQL ในไฟล์นี้และเทสต์ในโฟลเดอร์นี้มีไว้เตรียม/ตรวจข้อมูลเท่านั้น (โค้ดแอปเขียน SQL ได้แค่ใน app/repositories/)

แนวคิดจาก: ใหม่ (docs/PLAN.md งาน 7)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool

from app.repositories import Repos, make_repos
from app.repositories.db import close_pool, open_pool

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "20261022000000_init.sql"

# ทุกตารางใน migration (TRUNCATE ก่อนทุกเทสต์)
TABLES = (
    "ops_flags",
    "close_tokens",
    "feedback",
    "ai_steps",
    "ai_runs",
    "attachments",
    "messages",
    "conversation_state",
    "conversations",
    "line_raw_events",
    "line_consents",
    "line_users",
)


def migrate_up_sql(text: str) -> str:
    """ส่วนระหว่าง `-- migrate:up` กับ `-- migrate:down` ของไฟล์ migration (รูปแบบ dbmate)"""
    if "-- migrate:up" not in text or "-- migrate:down" not in text:
        raise ValueError("migration ต้องมี -- migrate:up และ -- migrate:down")
    return text.split("-- migrate:up", 1)[1].split("-- migrate:down", 1)[0]


if sys.platform == "win32":

    def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> dict[str, Any]:
        return {"selector": asyncio.SelectorEventLoop}


@pytest.fixture(scope="session")
def db_conninfo() -> Iterator[str]:
    """conninfo ที่ search_path ชี้ schema ทดสอบ (สร้าง/ลบต่อ session)"""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("ต้องตั้ง TEST_DATABASE_URL เพื่อรัน integration test ของ repositories")
    schema = f"test_repos_{secrets.token_hex(4)}"
    ident = sql.Identifier(schema)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(ident))
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL("SET search_path TO {}").format(ident))
            # ไม่มีพารามิเตอร์ -> simple query protocol รันหลายคำสั่งในครั้งเดียวได้
            conn.execute(migrate_up_sql(MIGRATION.read_text(encoding="utf-8")))  # type: ignore[arg-type]
        yield make_conninfo(url, options=f"-c search_path={schema}")
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))


@pytest.fixture
async def pool(db_conninfo: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = await open_pool(db_conninfo, min_size=1, max_size=4)
    try:
        async with pool.connection() as conn:
            await conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")  # type: ignore[arg-type]
        yield pool
    finally:
        await close_pool(pool)


@pytest.fixture
def repos(pool: AsyncConnectionPool) -> Repos:
    return make_repos(pool)


class Db:
    """ตัวช่วยของเทสต์: รัน SQL ตรงเพื่อเตรียม/ตรวจข้อมูล"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def fetch(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, params)  # type: ignore[arg-type]
            return list(await cur.fetchall()) if cur.description else []

    async def one(self, query: str, params: Any = None) -> dict[str, Any]:
        rows = await self.fetch(query, params)
        assert len(rows) == 1, rows
        return rows[0]

    async def scalar(self, query: str, params: Any = None) -> Any:
        return next(iter((await self.one(query, params)).values()))

    async def now(self) -> Any:
        """เวลาของ DB (repository ประทับเวลาด้วย now() ของ DB ไม่ใช่นาฬิกา Python)"""
        return await self.scalar("SELECT now()")


@pytest.fixture
def db(pool: AsyncConnectionPool) -> Db:
    return Db(pool)
