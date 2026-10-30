"""tests/repositories/test_migrations.py — migration ของ procrastinate / reply_tokens (D-010)

- (รันเสมอ) ส่วน migrate:up ของ 20261024000000_procrastinate_schema.sql = schema.sql ของ procrastinate ที่ติดตั้งอยู่
  (อัปเกรด procrastinate แล้วลืมเพิ่ม migration -> เทสต์นี้ล้ม) และเวอร์ชันตรงกับที่ pin ไว้
- (ต้องมี TEST_DATABASE_URL) ทุก migration ขึ้น -> ลง (ย้อนลำดับ) -> ขึ้นใหม่ ได้สะอาดใน schema ชั่วคราว
  และส่วน down ของ procrastinate ลบของ procrastinate ครบ
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import procrastinate
import psycopg
import pytest
from procrastinate.schema import SchemaManager
from psycopg import sql

from tests.repositories.conftest import (
    MIGRATIONS,
    MIGRATIONS_DIR,
    PROCRASTINATE_TABLES,
    migrate_up_sql,
)

PROCRASTINATE_MIGRATION = MIGRATIONS_DIR / "20261024000000_procrastinate_schema.sql"
PINNED_VERSION = "3.10.0"


def migrate_down_sql(text: str) -> str:
    return text.split("-- migrate:down", 1)[1]


def test_procrastinate_migration_is_packaged_schema() -> None:
    up = migrate_up_sql(PROCRASTINATE_MIGRATION.read_text(encoding="utf-8"))
    assert up.strip() == SchemaManager.get_schema().strip()


def test_procrastinate_version_is_pinned() -> None:
    assert procrastinate.__version__ == PINNED_VERSION
    header = PROCRASTINATE_MIGRATION.read_text(encoding="utf-8").split("-- migrate:up", 1)[0]
    assert PINNED_VERSION in header
    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"procrastinate=={PINNED_VERSION}"' in pyproject


def test_dbmate_markers_only_once() -> None:
    # dbmate หาบรรทัดที่ขึ้นต้นด้วย "-- migrate:up/down" — หัวไฟล์ห้ามมีบรรทัดแบบนั้นซ้ำ
    for path in MIGRATIONS:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert sum(line.startswith("-- migrate:up") for line in lines) == 1, path.name
        assert sum(line.startswith("-- migrate:down") for line in lines) == 1, path.name


@pytest.fixture
def scratch_schema() -> Iterator[tuple[psycopg.Connection, str]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("ต้องตั้ง TEST_DATABASE_URL เพื่อรัน integration test ของ migrations")
    schema = f"test_mig_{secrets.token_hex(4)}"
    ident = sql.Identifier(schema)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(ident))
        conn.execute(sql.SQL("SET search_path TO {}").format(ident))
        try:
            yield conn, schema
        finally:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))


def _objects(conn: psycopg.Connection, schema: str) -> dict[str, set[str]]:
    q = {
        "tables": "SELECT tablename FROM pg_tables WHERE schemaname = %s",
        "types": (
            "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = %s AND t.typtype IN ('e', 'c') "
            "AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = t.typrelid AND c.relkind <> 'c')"
        ),
        "functions": (
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = %s"
        ),
    }
    return {k: {r[0] for r in conn.execute(v, (schema,)).fetchall()} for k, v in q.items()}  # type: ignore[arg-type]


def test_all_migrations_up_down_up(scratch_schema: tuple[psycopg.Connection, str]) -> None:
    conn, schema = scratch_schema
    texts = [p.read_text(encoding="utf-8") for p in MIGRATIONS]
    for text in texts:
        conn.execute(migrate_up_sql(text))  # type: ignore[arg-type]
    up = _objects(conn, schema)
    assert set(PROCRASTINATE_TABLES) | {"reply_tokens"} <= up["tables"]
    assert {"procrastinate_job_status", "procrastinate_job_event_type", "procrastinate_job_to_defer_v1"} <= up["types"]
    assert {"procrastinate_fetch_job_v2", "procrastinate_defer_jobs_v1"} <= up["functions"]

    # ลงทีละไฟล์ย้อนลำดับ: ทุกไฟล์ต้องลงได้ และหลังลงของ procrastinate ต้องไม่เหลือของ procrastinate
    for path, text in reversed(list(zip(MIGRATIONS, texts, strict=True))):
        conn.execute(migrate_down_sql(text))  # type: ignore[arg-type]
        if path == PROCRASTINATE_MIGRATION:
            left = _objects(conn, schema)
            assert not {n for names in left.values() for n in names if n.startswith("procrastinate")}
    assert _objects(conn, schema) == {"tables": set(), "types": set(), "functions": set()}

    for text in texts:
        conn.execute(migrate_up_sql(text))  # type: ignore[arg-type]
    assert _objects(conn, schema) == up
