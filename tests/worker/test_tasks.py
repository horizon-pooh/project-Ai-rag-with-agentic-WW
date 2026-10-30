"""tests/worker/test_tasks.py — process_event / run_claimed / enqueue_event (fake + InMemoryConnector ของ procrastinate)"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from procrastinate.testing import InMemoryConnector

from app.pipeline import orchestrator
from app.pipeline.orchestrator import TurnOutcome, TurnRoute
from app.pipeline.pdpa import user_ref
from app.pipeline.schemas import ProcessStatus
from app.worker import app as worker_app
from app.worker import tasks
from tests.fakes import DEFAULT_NOW, make_deps
from tests.infra_fakes import FakeTurns, finish_all, make_queue, queued


@pytest.fixture
def turns(monkeypatch: pytest.MonkeyPatch) -> FakeTurns:
    fake = FakeTurns()
    monkeypatch.setattr(orchestrator, "run_turn", fake, raising=False)
    return fake


def _ctx(deps: Any) -> dict[str, Any]:
    return {"deps": deps, "pipeline_cfg": object()}


async def _insert(deps: Any, n: int, *, user: str | None = "U1", minutes: int = 0) -> int:
    rid = await deps.raw_events.insert(
        webhook_event_id=f"wh-{n}",
        line_user_id=user,
        event_type="message",
        payload={"type": "message", "n": n},
        occurred_at=DEFAULT_NOW + timedelta(minutes=minutes),
    )
    assert rid is not None
    return rid


def _status(deps: Any, rid: int) -> ProcessStatus:
    return deps.raw_events.db.raw_events[rid].process_status


async def test_processes_pending_oldest_first(turns: FakeTurns) -> None:
    deps = make_deps()
    late = await _insert(deps, 1, minutes=5)
    early = await _insert(deps, 2, minutes=1)
    mid = await _insert(deps, 3, minutes=3)
    other_user = await _insert(deps, 4, user="U2", minutes=0)
    await tasks.process_event(_ctx(deps), late)
    assert turns.calls == [early, mid, late]
    assert all(_status(deps, r) is ProcessStatus.DONE for r in (early, mid, late))
    assert _status(deps, other_user) is ProcessStatus.PENDING


async def test_skips_event_claimed_by_someone_else(turns: FakeTurns) -> None:
    deps = make_deps()
    a = await _insert(deps, 1, minutes=1)
    b = await _insert(deps, 2, minutes=2)
    await deps.raw_events.claim(a)  # fast path ทำอยู่
    await tasks.process_event(_ctx(deps), b)
    assert turns.calls == [b]
    assert _status(deps, a) is ProcessStatus.PROCESSING


async def test_job_for_done_event_still_drains_user(turns: FakeTurns) -> None:
    deps = make_deps()
    a = await _insert(deps, 1, minutes=1)
    b = await _insert(deps, 2, minutes=2)
    await deps.raw_events.claim(a)
    await deps.raw_events.mark_done(a)
    await tasks.process_event(_ctx(deps), a)
    assert turns.calls == [b]


async def test_missing_raw_returns(turns: FakeTurns) -> None:
    deps = make_deps()
    await tasks.process_event(_ctx(deps), 999)
    assert turns.calls == []


async def test_no_user_runs_only_that_event(turns: FakeTurns) -> None:
    deps = make_deps()
    rid = await _insert(deps, 1, user=None)
    other = await _insert(deps, 2, user="U1")
    await tasks.process_event(_ctx(deps), rid)
    assert turns.calls == [rid]
    assert _status(deps, rid) is ProcessStatus.DONE
    assert _status(deps, other) is ProcessStatus.PENDING


async def test_no_user_failure_released_then_retry(turns: FakeTurns) -> None:
    deps = make_deps()
    rid = await _insert(deps, 1, user=None)
    turns.errors[rid] = [RuntimeError("boom")]
    with pytest.raises(tasks.EventRetry):
        await tasks.process_event(_ctx(deps), rid)
    assert _status(deps, rid) is ProcessStatus.PENDING
    await tasks.process_event(_ctx(deps), rid)
    assert _status(deps, rid) is ProcessStatus.DONE


async def test_failure_released_then_failed_after_three_attempts(turns: FakeTurns) -> None:
    deps = make_deps()
    a = await _insert(deps, 1, minutes=1)
    b = await _insert(deps, 2, minutes=2)
    turns.errors[a] = [RuntimeError("db down 0812345678")] * 3
    ctx = _ctx(deps)

    # ครั้งที่ 1, 2: release กลับเป็น pending แล้ว EventRetry; b ไม่ถูกทำก่อน a (รักษาลำดับ)
    for attempt in (1, 2):
        with pytest.raises(tasks.EventRetry):
            await tasks.process_event(ctx, b)
        row = deps.raw_events.db.raw_events[a]
        assert row.process_status is ProcessStatus.PENDING
        assert row.attempts == attempt
        assert row.error and "RuntimeError" in row.error
        assert b not in turns.calls

    # ครั้งที่ 3: failed แล้วทำ b ต่อ ไม่ retry
    await tasks.process_event(ctx, b)
    row = deps.raw_events.db.raw_events[a]
    assert row.process_status is ProcessStatus.FAILED
    assert row.attempts == 3
    assert "0812345678" not in (row.error or "")  # error ผ่าน redact
    assert _status(deps, b) is ProcessStatus.DONE
    assert turns.calls == [a, a, a, b]


async def test_skipped_outcome_marks_skipped(turns: FakeTurns) -> None:
    turns.outcome = TurnOutcome(route=TurnRoute.SKIPPED_EVENT.value)
    deps = make_deps()
    rid = await _insert(deps, 1)
    await tasks.process_event(_ctx(deps), rid)
    assert _status(deps, rid) is ProcessStatus.SKIPPED


async def test_batch_budget_stops_after_first(
    turns: FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "BATCH_BUDGET_S", -1)
    deps = make_deps()
    a = await _insert(deps, 1, minutes=1)
    b = await _insert(deps, 2, minutes=2)
    await tasks.process_event(_ctx(deps), b)
    assert turns.calls == [a]
    assert _status(deps, b) is ProcessStatus.PENDING


# --- enqueue_event ---


def test_locks() -> None:
    assert tasks.queueing_lock_for(42) == "raw:42"
    lock = tasks.user_lock_for("U0123456789abcdef")
    assert lock == f"user:{user_ref('U0123456789abcdef')}"
    # ไม่มี line_user_id ตัวจริงในตาราง procrastinate_jobs
    assert "U0123456789abcdef" not in (lock or "")
    assert tasks.user_lock_for("U1") != tasks.user_lock_for("U2")
    assert tasks.user_lock_for(None) is None
    assert tasks.user_lock_for("") is None


async def test_enqueue_event_defer_args_on_real_task() -> None:
    # App ตัวจริงของ worker (งาน process_event ลงทะเบียนแล้ว) บน InMemoryConnector
    connector = InMemoryConnector()
    with worker_app.app.replace_connector(connector) as queue:
        assert await tasks.enqueue_event(queue, 42, "U1") is True
        assert await tasks.enqueue_event(queue, 43, None) is True
    rows = [connector.jobs[i] for i in sorted(connector.jobs)]
    assert [(r["task_name"], r["queue_name"], r["args"], r["lock"], r["queueing_lock"]) for r in rows] == [
        ("process_event", "events", {"raw_event_id": 42}, tasks.user_lock_for("U1"), "raw:42"),
        ("process_event", "events", {"raw_event_id": 43}, None, "raw:43"),
    ]


async def test_enqueue_event_already_enqueued_returns_false() -> None:
    queue, connector = make_queue()
    assert await tasks.enqueue_event(queue, 42, "U1") is True
    # งานเดิมยังรอในคิว (todo) -> AlreadyEnqueued -> False ไม่ยก error
    assert await tasks.enqueue_event(queue, 42, "U1") is False
    assert len(queued(connector)) == 1
    # raw อื่นของ user เดียวกันใส่ได้ (lock เดียวกันไม่ห้ามใส่ ห้ามแค่รันพร้อมกัน)
    assert await tasks.enqueue_event(queue, 43, "U1") is True
    # งานเดิมจบแล้ว -> ใส่ id เดิมได้อีก (เช่น cron requeue หลัง release)
    finish_all(connector)
    assert await tasks.enqueue_event(queue, 42, "U1") is True


async def test_enqueue_event_other_errors_propagate() -> None:
    queue, _ = make_queue(fail=True)
    with pytest.raises(Exception, match="db down"):
        await tasks.enqueue_event(queue, 1, "U1")
