"""tests/worker/test_app.py — คิว procrastinate ของ worker (app/worker/app.py) บน InMemoryConnector ไม่ต่อ DB

- งาน / งาน periodic ลงทะเบียนครบ ค่า retry / lock / queueing_lock ถูก
- ตัวห่องาน (process_event_job + periodic) เรียกฟังก์ชันจริงใน tasks.py / cron.py ด้วยของจาก additional_context
- run_worker ประกอบ deps -> เปิดคิวบน pool -> run_worker_async -> ปิดทุกอย่าง
- worker ในหน่วยความจำรันงานจริงตามลำดับของ lock (integration กับ Postgres จริงอยู่ที่ tests/repositories/test_pg_queue.py)
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from procrastinate.periodic import PeriodicRegistry
from procrastinate.testing import InMemoryConnector

from app.pipeline import orchestrator
from app.pipeline.schemas import ProcessStatus
from app.worker import app as worker_app
from app.worker import cron, tasks
from tests.fakes import DEFAULT_NOW, make_deps, make_settings
from tests.infra_fakes import FakeTurns

EXPECTED_PERIODIC = {
    "requeue_pending": "* * * * * 5",
    "release_stuck": "*/2 * * * * 35",
    "handoff_timeout": "*/5 * * * * 15",
    # 03:30 เวลาไทย (cron ของ procrastinate เป็น UTC)
    "retention": "30 20 * * *",
    "retry_stalled_jobs": "* * * * * 50",
    "remove_old_jobs": "0 21 * * *",
}


def test_process_event_task_registered() -> None:
    task = worker_app.app.tasks[tasks.PROCESS_EVENT_TASK]
    assert task is worker_app.process_event_job
    assert task.queue == worker_app.EVENTS_QUEUE
    assert task.pass_context is True
    # lock / queueing_lock มาจาก enqueue_event ต่อ event ไม่ใช่ค่าตั้งต้นของงาน
    assert task.lock is None and task.queueing_lock is None
    strategy = task.retry_strategy
    assert strategy is worker_app.EVENT_RETRY
    assert strategy.max_attempts == worker_app.MAX_JOB_ATTEMPTS == 10
    assert strategy.wait == worker_app.RETRY_WAIT_S


def test_periodic_tasks_registered() -> None:
    registry = worker_app.app.periodic_registry.periodic_tasks
    assert {name: pt.cron for (name, _), pt in registry.items()} == EXPECTED_PERIODIC
    for (name, _), pt in registry.items():
        assert pt.task.queue == worker_app.MAINTENANCE_QUEUE
        # ไม่กองในคิว + ไม่รันซ้อนตัวเอง
        assert pt.task.queueing_lock == name
        assert pt.task.lock == f"maintenance:{name}"
        assert pt.task.pass_context is True


def test_concurrency_and_job_timeout() -> None:
    assert worker_app.concurrency_for(make_settings(ai_concurrency=1)) == 4
    assert worker_app.concurrency_for(make_settings(ai_concurrency=4)) == 8
    assert worker_app.job_timeout_for(make_settings(turn_deadline_s=50)) == 110


def test_worker_options() -> None:
    ctx = {"deps": object()}
    opts = worker_app.worker_options(make_settings(ai_concurrency=3), ctx)
    assert opts == {
        "name": "worker",
        "concurrency": 6,
        "additional_context": ctx,
        "delete_jobs": "successful",
        "shutdown_graceful_timeout": worker_app.SHUTDOWN_GRACEFUL_TIMEOUT_S,
    }


def test_default_connector_reads_database_url_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(database_url="postgresql://u:p@db:5432/x")
    monkeypatch.setattr(worker_app, "get_settings", lambda: settings)
    assert worker_app._conninfo() == "postgresql://u:p@db:5432/x"


def _job_context(ctx: dict[str, Any], app: Any = None) -> Any:
    return SimpleNamespace(additional_context=ctx, app=app or worker_app.app)


async def test_process_event_job_calls_task_under_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[dict[str, Any], int]] = []

    async def fake(ctx: dict[str, Any], raw_event_id: int) -> None:
        seen.append((ctx, raw_event_id))

    monkeypatch.setattr(tasks, "process_event", fake)
    ctx = {"deps": make_deps(), "pipeline_cfg": object()}
    await worker_app.process_event_job(_job_context(ctx), raw_event_id=7)
    assert seen == [(ctx, 7)]

    async def slow(ctx: dict[str, Any], raw_event_id: int) -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(tasks, "process_event", slow)
    monkeypatch.setattr(tasks, "JOB_TIMEOUT_MARGIN_S", 0)
    ctx = {"deps": make_deps(settings=make_settings(turn_deadline_s=0.05)), "pipeline_cfg": object()}
    with pytest.raises(TimeoutError):
        await worker_app.process_event_job(_job_context(ctx), raw_event_id=7)


async def test_periodic_wrappers_call_cron_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def recorder(name: str) -> Any:
        async def fn(*args: Any, **kwargs: Any) -> int:
            calls.append((name, args + tuple(kwargs.values())))
            return 0

        return fn

    for name in ("requeue_pending", "release_stuck", "handoff_timeout", "retention", "retry_stalled_jobs"):
        monkeypatch.setattr(cron, name, recorder(name))
    monkeypatch.setattr(worker_app.builtin_tasks, "remove_old_jobs", recorder("remove_old_jobs"))

    deps = make_deps()
    raw_ret, tok_ret = object(), object()
    queue = SimpleNamespace(job_manager=object())
    jc = _job_context(
        {"deps": deps, "raw_retention": raw_ret, "reply_token_retention": tok_ret}, app=queue
    )
    await worker_app.requeue_pending_job(jc, timestamp=0)
    await worker_app.release_stuck_job(jc, timestamp=0)
    await worker_app.handoff_timeout_job(jc, timestamp=0)
    await worker_app.retention_job(jc, timestamp=0)
    await worker_app.retry_stalled_jobs_job(jc, timestamp=0)
    await worker_app.remove_old_jobs_job(jc, timestamp=0)
    assert calls == [
        ("requeue_pending", (deps, queue)),
        ("release_stuck", (deps, queue)),
        ("handoff_timeout", (deps,)),
        ("retention", (deps, raw_ret, tok_ret)),
        ("retry_stalled_jobs", (queue.job_manager,)),
        ("remove_old_jobs", (jc, worker_app.OLD_JOBS_MAX_HOURS, True, True, True)),
    ]


async def test_open_queue_uses_given_pool_and_restores_connector() -> None:
    original = worker_app.app.connector
    pool = object()
    async with worker_app.open_queue(pool) as queue:  # type: ignore[arg-type]
        assert queue is worker_app.app
        assert queue.connector is not original
        assert queue.connector.pool is pool
    assert worker_app.app.connector is original


async def test_run_worker_startup_and_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = make_deps()
    pool = object()
    res = SimpleNamespace(
        pool=pool, repos=SimpleNamespace(raw_events=object()), reply_tokens=object()
    )
    cfg = object()
    events: list[str] = []
    seen: dict[str, Any] = {}

    async def build(settings: Any) -> tuple[Any, Any]:
        events.append("build")
        return deps, res

    async def close(r: Any) -> None:
        events.append("close")
        assert r is res

    async def run_worker_async(**kwargs: Any) -> None:
        events.append("run")
        seen.update(kwargs)
        seen["pool"] = worker_app.app.connector.pool

    monkeypatch.setattr(worker_app, "build_deps", build)
    monkeypatch.setattr(worker_app, "close_resources", close)
    monkeypatch.setattr(worker_app, "init_observability", lambda s: events.append("obs"))
    monkeypatch.setattr(orchestrator, "load_pipeline_config", lambda s: cfg, raising=False)
    monkeypatch.setattr(worker_app.app, "run_worker_async", run_worker_async)

    await worker_app.run_worker(make_settings(ai_concurrency=1))
    assert events == ["obs", "build", "run", "close"]
    assert seen["pool"] is pool
    assert seen["concurrency"] == 4
    assert seen["delete_jobs"] == "successful"
    assert seen["additional_context"] == {
        "deps": deps,
        "pipeline_cfg": cfg,
        "raw_retention": res.repos.raw_events,
        "reply_token_retention": res.reply_tokens,
    }


async def test_run_worker_closes_resources_when_worker_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    res = SimpleNamespace(pool=object(), repos=SimpleNamespace(raw_events=object()), reply_tokens=object())
    closed: list[Any] = []

    async def build(settings: Any) -> tuple[Any, Any]:
        return make_deps(), res

    async def close(r: Any) -> None:
        closed.append(r)

    async def boom(**kwargs: Any) -> None:
        raise RuntimeError("worker crashed")

    monkeypatch.setattr(worker_app, "build_deps", build)
    monkeypatch.setattr(worker_app, "close_resources", close)
    monkeypatch.setattr(worker_app, "init_observability", lambda s: None)
    monkeypatch.setattr(orchestrator, "load_pipeline_config", lambda s: object(), raising=False)
    monkeypatch.setattr(worker_app.app, "run_worker_async", boom)
    with pytest.raises(RuntimeError):
        await worker_app.run_worker(make_settings())
    assert closed == [res]


async def test_in_memory_worker_runs_events_with_user_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """worker จริงของ procrastinate (ในหน่วยความจำ) + งาน process_event จริง + fake deps"""
    turns = FakeTurns()

    async def slow_turn(deps: Any, raw: Any, cfg: Any) -> Any:
        # ช้าพอให้ worker ลองหยิบงานถัดไปขณะงานนี้ยัง doing
        await asyncio.sleep(0.2)
        return await turns(deps, raw, cfg)

    monkeypatch.setattr(orchestrator, "run_turn", slow_turn, raising=False)
    # ไม่ให้ตัวตั้งเวลา periodic ใส่งานบำรุงรักษาระหว่างเทสต์
    monkeypatch.setattr(worker_app.app, "periodic_registry", PeriodicRegistry())
    deps = make_deps()

    async def insert(n: int, user: str, minutes: int) -> int:
        rid = await deps.raw_events.insert(
            webhook_event_id=f"wh-{n}",
            line_user_id=user,
            event_type="message",
            payload={"type": "message"},
            occurred_at=DEFAULT_NOW + timedelta(minutes=minutes),
        )
        assert rid is not None
        return rid

    a = await insert(1, "U1", 1)
    b = await insert(2, "U1", 2)
    c = await insert(3, "U2", 1)
    connector = InMemoryConnector()
    with worker_app.app.replace_connector(connector) as queue:
        for rid, user in ((a, "U1"), (b, "U1"), (c, "U2")):
            assert await tasks.enqueue_event(queue, rid, user)
        opts = worker_app.worker_options(deps.settings, {"deps": deps, "pipeline_cfg": object()})
        opts.update(wait=False, install_signal_handlers=False, listen_notify=False)
        # รอบแรก: งานของ b ติด lock ของ U1 (งานของ a กำลังทำ) -> ยังไม่ถูกหยิบ
        await queue.run_worker_async(**opts)
        assert [row["args"]["raw_event_id"] for row in connector.jobs.values()] == [b]
        # รอบสอง: งานของ b รันได้ (event b ถูกงานของ a ทำไปแล้ว -> ไม่มีอะไรค้าง)
        await queue.run_worker_async(**opts)
    assert connector.jobs == {}  # สำเร็จแล้วลบทิ้ง (delete_jobs=successful)
    assert [r for r in turns.calls if r in (a, b)] == [a, b]
    assert sorted(turns.calls) == sorted([a, b, c])
    for rid in (a, b, c):
        assert deps.raw_events.db.raw_events[rid].process_status is ProcessStatus.DONE


def test_main_runs_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker import __main__ as entry

    settings = make_settings()
    seen: list[Any] = []

    async def fake_run(s: Any) -> None:
        seen.append(s)

    monkeypatch.setattr(entry, "get_settings", lambda: settings)
    monkeypatch.setattr(entry, "run_worker", fake_run)
    entry.main()
    assert seen == [settings]
