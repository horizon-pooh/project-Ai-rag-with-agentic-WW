"""tests/repositories/test_pg_queue.py — คิว procrastinate บน Postgres จริง (schema จาก migration ของเรา, D-010)

ข้ามทั้งไฟล์ถ้าไม่ได้ตั้ง TEST_DATABASE_URL (fixture db_conninfo ใน conftest.py)
- งานที่ lock เดียวกัน (user เดียวกัน) ไม่รันพร้อมกันและเรียงตามลำดับที่ใส่ ส่วน user อื่นรันคู่กันได้
- queueing_lock กันใส่ซ้ำ (AlreadyEnqueued -> enqueue_event คืน False)
- end-to-end: enqueue_event -> worker จริง (app ของเรา, งาน process_event จริง, repo line_raw_events จริง)
  -> event ของ user เดียวกันทำทีละอันตามลำดับ
worker รันเป็น asyncio task แล้ว cancel เมื่อเสร็จ (วิธีหยุดตามเอกสาร procrastinate)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from procrastinate import App, JobContext, PsycopgConnector
from procrastinate.periodic import PeriodicRegistry
from psycopg_pool import AsyncConnectionPool

from app.pipeline import orchestrator
from app.pipeline.orchestrator import TurnOutcome
from app.pipeline.schemas import ProcessStatus
from app.repositories import Repos
from app.worker import app as worker_app
from app.worker import tasks
from tests.fakes import make_deps
from tests.repositories.conftest import Db

T0 = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)
WAIT_S = 20.0
FAST_POLL = {
    "install_signal_handlers": False,
    "fetch_job_polling_interval": 0.05,
    "abort_job_polling_interval": 0.5,
}


class Tracker:
    """บันทึกช่วงเวลาที่แต่ละงานรัน แยกตาม key (lock / user)"""

    def __init__(self) -> None:
        self.running: dict[str, int] = {}
        self.max_running: dict[str, int] = {}
        self.total = 0
        self.max_total = 0
        self.order: list[tuple[str, int]] = []
        self.done = 0

    async def run(self, key: str, item: int, sleep_s: float) -> None:
        self.running[key] = self.running.get(key, 0) + 1
        self.max_running[key] = max(self.max_running.get(key, 0), self.running[key])
        self.total += 1
        self.max_total = max(self.max_total, self.total)
        self.order.append((key, item))
        try:
            await asyncio.sleep(sleep_s)
        finally:
            self.running[key] -= 1
            self.total -= 1
            self.done += 1


async def _run_worker_until(
    app: App, done: Callable[[], Awaitable[bool]], **options: Any
) -> None:
    worker = asyncio.create_task(app.run_worker_async(**{**FAST_POLL, **options}))
    try:
        deadline = time.monotonic() + WAIT_S
        while not await done():
            if worker.done():
                worker.result()  # ยก error ของ worker ออกมา
                raise AssertionError("worker หยุดก่อนงานเสร็จ")
            if time.monotonic() > deadline:
                raise AssertionError("งานไม่เสร็จในเวลา")
            await asyncio.sleep(0.05)
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


async def test_same_lock_runs_one_at_a_time_in_order(pool: AsyncConnectionPool, db: Db) -> None:
    app = App(connector=PsycopgConnector())
    tracker = Tracker()

    @app.task(name="probe", pass_context=True)
    async def probe(context: JobContext, n: int) -> None:
        await tracker.run(context.job.lock or "-", n, 0.3)

    await app.open_async(pool)
    u1, u2 = tasks.user_lock_for("U1"), tasks.user_lock_for("U2")
    assert u1 and u2
    for n in (1, 2, 3):
        await probe.configure(lock=u1).defer_async(n=n)
    await probe.configure(lock=u2).defer_async(n=4)

    async def all_done() -> bool:
        return tracker.done == 4

    await _run_worker_until(app, all_done, concurrency=4)
    # user เดียวกัน: ไม่ซ้อน และเรียงตามลำดับที่ใส่คิว
    assert tracker.max_running[u1] == 1
    assert [n for key, n in tracker.order if key == u1] == [1, 2, 3]
    # user อื่นรันคู่กับ U1 ได้ (worker ไม่ได้ทำทีละงานทั้งหมด)
    assert tracker.max_total >= 2
    statuses = await db.fetch("SELECT status::text AS s, count(*) AS n FROM procrastinate_jobs GROUP BY 1")
    assert statuses == [{"s": "succeeded", "n": 4}]


async def test_queueing_lock_dedupes_on_postgres(pool: AsyncConnectionPool, db: Db) -> None:
    async with worker_app.open_queue(pool) as queue:
        assert await tasks.enqueue_event(queue, 1, "U1") is True
        assert await tasks.enqueue_event(queue, 1, "U1") is False
        assert await tasks.enqueue_event(queue, 2, "U1") is True
    rows = await db.fetch(
        "SELECT task_name, queue_name, lock, queueing_lock, args FROM procrastinate_jobs ORDER BY id"
    )
    assert rows == [
        {
            "task_name": "process_event",
            "queue_name": "events",
            "lock": tasks.user_lock_for("U1"),
            "queueing_lock": "raw:1",
            "args": {"raw_event_id": 1},
        },
        {
            "task_name": "process_event",
            "queue_name": "events",
            "lock": tasks.user_lock_for("U1"),
            "queueing_lock": "raw:2",
            "args": {"raw_event_id": 2},
        },
    ]
    # ไม่มี line_user_id ตัวจริงในตารางของ procrastinate
    assert all("U1" not in str(r["lock"]) for r in rows)


async def test_end_to_end_events_of_one_user_never_overlap(
    pool: AsyncConnectionPool, repos: Repos, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = Tracker()

    async def slow_turn(deps: Any, raw: Any, cfg: Any) -> TurnOutcome:
        await tracker.run(raw.line_user_id, raw.id, 0.2)
        return TurnOutcome(route="ai_answer")

    monkeypatch.setattr(orchestrator, "run_turn", slow_turn, raising=False)
    # ไม่ให้ตัวตั้งเวลา periodic ใส่งานบำรุงรักษาระหว่างเทสต์
    monkeypatch.setattr(worker_app.app, "periodic_registry", PeriodicRegistry())
    deps = make_deps(raw_events=repos.raw_events)

    ids: dict[str, list[int]] = {"U1": [], "U2": []}
    for i, user in enumerate(["U1", "U2", "U1", "U1"]):
        rid = await repos.raw_events.insert(
            webhook_event_id=f"wh-{i}",
            line_user_id=user,
            event_type="message",
            payload={"type": "message"},
            occurred_at=T0 + timedelta(seconds=i),
        )
        assert rid is not None
        ids[user].append(rid)

    async with worker_app.open_queue(pool) as queue:
        for user, rids in ids.items():
            for rid in rids:
                assert await tasks.enqueue_event(queue, rid, user)

        async def all_done() -> bool:
            for rids in ids.values():
                for rid in rids:
                    raw = await repos.raw_events.get(rid)
                    if raw is None or raw.process_status is not ProcessStatus.DONE:
                        return False
            # งานของ event ที่ถูกงานก่อนหน้าทำไปแล้วก็ต้องรันจบ (และถูกลบเพราะสำเร็จ: delete_jobs=successful)
            return await db.scalar("SELECT count(*) FROM procrastinate_jobs") == 0

        opts = worker_app.worker_options(deps.settings, {"deps": deps, "pipeline_cfg": object()})
        await _run_worker_until(queue, all_done, **opts)

    assert tracker.max_running["U1"] == 1
    assert [rid for key, rid in tracker.order if key == "U1"] == ids["U1"]
    assert tracker.max_total >= 2
    # ทุก event ทำครั้งเดียว (งานของ event ที่ถูกทำไปแล้วไม่ทำซ้ำ: claim ใน DB)
    assert sorted(rid for _, rid in tracker.order) == sorted(ids["U1"] + ids["U2"])
