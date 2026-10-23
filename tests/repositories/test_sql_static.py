"""tests/repositories/test_sql_static.py — ตรวจ repositories แบบไม่ต้องมี Postgres (รันเสมอ ไม่ข้าม)

- ทุกคลาส Pg*Repo ผ่าน isinstance กับ Protocol (runtime_checkable) ของมันใน app/pipeline/ports.py
- signature ของทุกเมธอดตรงกับ Protocol (ชื่อ/ชนิดพารามิเตอร์/ค่าตั้งต้น/annotation)
- รายชื่อคอลัมน์ที่ SELECT/RETURNING ตรงกับฟิลด์ของ model (model เป็น extra='forbid') และมีจริงใน migration
- คอลัมน์ใน INSERT (...) และ UPDATE ... SET ของทุกไฟล์มีจริงในตารางนั้นของ migration (กันพิมพ์ชื่อผิด)
- เทสต์ในโฟลเดอร์นี้ได้ SelectorEventLoop (psycopg async บน Windows ต้องใช้)
"""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import fields
from pathlib import Path

import pytest

from app.pipeline import ports
from app.pipeline.schemas import AiStepRecord, Conversation, RawEvent, StateRecord, User
from app.repositories import (
    PgAiRunsRepo,
    PgConversationsRepo,
    PgFeedbackRepo,
    PgFlagsRepo,
    PgMessagesRepo,
    PgRawEventsRepo,
    PgStateRepo,
    PgUsersRepo,
    Repos,
    make_repos,
)
from app.repositories.ai_runs import AI_STEP_COLUMNS
from app.repositories.conversations import CONVERSATION_COLUMNS
from app.repositories.raw_events import RAW_EVENT_COLUMNS
from app.repositories.state import STATE_COLUMNS
from app.repositories.users import USER_COLUMNS
from tests.repositories.conftest import MIGRATIONS, TABLES, migrate_up_sql

REPO_DIR = Path(__file__).resolve().parents[2] / "app" / "repositories"

PAIRS = [
    (PgRawEventsRepo, ports.RawEventsRepo),
    (PgUsersRepo, ports.UsersRepo),
    (PgConversationsRepo, ports.ConversationsRepo),
    (PgStateRepo, ports.StateRepo),
    (PgMessagesRepo, ports.MessagesRepo),
    (PgAiRunsRepo, ports.AiRunsRepo),
    (PgFeedbackRepo, ports.FeedbackRepo),
    (PgFlagsRepo, ports.FlagsRepo),
]
_IDS = [impl.__name__ for impl, _ in PAIRS]


class _DummyPool:
    """แทน AsyncConnectionPool: constructor ของ repository แค่เก็บ pool ไว้ ไม่ต่อ DB"""


def _protocol_methods(proto: type) -> list[str]:
    return [n for n, v in vars(proto).items() if not n.startswith("_") and inspect.isfunction(v)]


def _migration_tables() -> dict[str, set[str]]:
    """{ตาราง: {คอลัมน์}} จาก CREATE TABLE ในส่วน migrate:up ของทุกไฟล์ migration"""
    up = "\n".join(migrate_up_sql(p.read_text(encoding="utf-8")) for p in MIGRATIONS)
    tables: dict[str, set[str]] = {}
    for name, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", up, flags=re.S):
        cols = set()
        for line in body.splitlines():
            m = re.match(
                r"\s*([a-z_]+)\s+(bigint|text|timestamptz|jsonb|boolean|integer|numeric)\b", line
            )
            if m:
                cols.add(m.group(1))
        tables[name] = cols
    return tables


TABLE_COLUMNS = _migration_tables()


def test_migration_parsed_all_tables() -> None:
    assert set(TABLE_COLUMNS) == set(TABLES)
    assert all(TABLE_COLUMNS.values())


@pytest.mark.parametrize(("impl", "proto"), PAIRS, ids=_IDS)
def test_repo_satisfies_protocol(impl: type, proto: type) -> None:
    assert isinstance(impl(_DummyPool()), proto)


@pytest.mark.parametrize(("impl", "proto"), PAIRS, ids=_IDS)
def test_repo_signatures_match_protocol(impl: type, proto: type) -> None:
    for name in _protocol_methods(proto):
        want = inspect.signature(getattr(proto, name))
        got = inspect.signature(getattr(impl, name))
        assert inspect.iscoroutinefunction(getattr(impl, name)), name
        assert [(p.name, p.kind, p.default, p.annotation) for p in got.parameters.values()] == [
            (p.name, p.kind, p.default, p.annotation) for p in want.parameters.values()
        ], f"{impl.__name__}.{name}"
        assert got.return_annotation == want.return_annotation, f"{impl.__name__}.{name}"


def test_repos_cover_deps_repo_fields() -> None:
    deps_types = {f.name: f.type for f in fields(ports.Deps)}
    repos = make_repos(_DummyPool())  # type: ignore[arg-type]
    for f in fields(Repos):
        proto = getattr(ports, deps_types[f.name])
        assert isinstance(getattr(repos, f.name), proto), f.name
    # Deps ทุกฟิลด์ที่เป็น *Repo มีใน Repos
    assert {n for n, t in deps_types.items() if str(t).endswith("Repo")} == {
        f.name for f in fields(Repos)
    }


@pytest.mark.parametrize(
    ("cols", "model", "table"),
    [
        (RAW_EVENT_COLUMNS, RawEvent, "line_raw_events"),
        (USER_COLUMNS, User, "line_users"),
        (CONVERSATION_COLUMNS, Conversation, "conversations"),
        (STATE_COLUMNS, StateRecord, "conversation_state"),
        (AI_STEP_COLUMNS, AiStepRecord, "ai_steps"),
    ],
    ids=["raw_events", "users", "conversations", "state", "ai_steps"],
)
def test_columns_match_model_and_migration(cols: tuple[str, ...], model: type, table: str) -> None:
    assert len(set(cols)) == len(cols)
    assert set(cols) == set(model.model_fields)  # type: ignore[attr-defined]
    assert set(cols) <= TABLE_COLUMNS[table]


def _sources() -> list[tuple[str, str]]:
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(REPO_DIR.glob("*.py"))]


def test_insert_columns_exist_in_migration() -> None:
    found = 0
    for fname, src in _sources():
        for table, cols in re.findall(r"INSERT INTO (\w+)\s*\(([\w\s,]+)\)", src):
            found += 1
            assert table in TABLE_COLUMNS, f"{fname}: {table}"
            for col in (c.strip() for c in cols.split(",")):
                assert col in TABLE_COLUMNS[table], f"{fname}: {table}.{col}"
    assert found >= 8


def test_update_set_columns_exist_in_migration() -> None:
    found = 0
    for fname, src in _sources():
        pattern = r"\bUPDATE (?!SET\b)(\w+)(?: AS \w+)?\s+SET\s+(.*?)\s+(?:WHERE|FROM)\b"
        for table, assignments in re.findall(pattern, src, flags=re.S):
            found += 1
            assert table in TABLE_COLUMNS, f"{fname}: {table}"
            for col in re.findall(r"(?:^|,)\s*(\w+)\s*=", assignments):
                assert col in TABLE_COLUMNS[table], f"{fname}: {table}.{col}"
    assert found >= 10


async def test_event_loop_is_selector() -> None:
    # psycopg async ใช้ ProactorEventLoop (ค่าตั้งต้นของ Windows) ไม่ได้ — conftest ตั้งให้
    assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
