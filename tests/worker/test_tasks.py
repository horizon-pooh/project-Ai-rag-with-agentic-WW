"""tests/worker/test_tasks.py — process_event / run_claimed / enqueue_event (fake ทั้งหมด)"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from arq import Retry

from app.pipeline import orchestrator
from app.pipeline.orchestrator import TurnOutcome, TurnRoute
from app.pipeline.schemas import ProcessStatus
from app.worker import tasks
from app.worker.locks import UserLock
from tests.fakes import DEFAULT_NOW, make_deps
from tests.infra_fakes import FakeArqPool, FakeRedis, FakeTurns


@pytest.fixture
def turns(monkeypatch: pytest.MonkeyPatch) -> FakeTurns:
    fake = FakeTurns()
    monkeypatch.setattr(orchestrator, "run_turn", fake, raising=False)
    return fake


def _ctx(deps: Any, redis: FakeRedis | None = None) -> dict[str, Any]:
    redis = redis or FakeRedis()
    return {
        "deps": deps,
        "pipeline_cfg": object(),
        "user_lock": UserLock(redis, poll_s=0.01),
        "fake_redis": redis,
    }


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
    ctx = _ctx(deps)
    await tasks.process_event(ctx, late)
    assert turns.calls == [early, mid, late]
    assert all(_status(deps, r) is ProcessStatus.DONE for r in (early, mid, late))
    assert _status(deps, other_user) is ProcessStatus.PENDING
    # ปล่อยล็อกแล้ว
    assert await ctx["fake_redis"].get("lock:user:U1") is None


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


async def test_no_user_runs_without_lock(turns: FakeTurns) -> None:
    deps = make_deps()
    rid = await _insert(deps, 1, user=None)
    ctx = _ctx(deps)
    await tasks.process_event(ctx, rid)
    assert turns.calls == [rid]
    assert _status(deps, rid) is ProcessStatus.DONE
    assert ctx["fake_redis"].count("set") == 0


async def test_lock_busy_raises_retry(turns: FakeTurns, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "LOCK_WAIT_S", 0.05)
    deps = make_deps()
    rid = await _insert(deps, 1)
    redis = FakeRedis()
    await redis.set("lock:user:U1", "other-worker", px=60_000)
    with pytest.raises(Retry):
        await tasks.process_event(_ctx(deps, redis), rid)
    assert turns.calls == []
    assert _status(deps, rid) is ProcessStatus.PENDING


async def test_failure_released_then_failed_after_three_attempts(turns: FakeTurns) -> None:
    deps = make_deps()
    a = await _insert(deps, 1, minutes=1)
    b = await _insert(deps, 2, minutes=2)
    turns.errors[a] = [RuntimeError("db down 0812345678")] * 3
    ctx = _ctx(deps)

    # ครั้งที่ 1, 2: release กลับเป็น pending แล้ว Retry; b ไม่ถูกทำก่อน a (รักษาลำดับ)
    for attempt in (1, 2):
        with pytest.raises(Retry):
            await tasks.process_event(ctx, b)
        row = deps.raw_events.db.raw_events[a]
        assert row.process_status is ProcessStatus.PENDING
        assert row.attempts == attempt
        assert row.error and "RuntimeError" in row.error
        assert b not in turns.calls
        assert await ctx["fake_redis"].get("lock:user:U1") is None

    # ครั้งที่ 3: failed แล้วทำ b ต่อ ไม่ Retry
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


async def test_enqueue_event_uses_raw_job_id() -> None:
    pool = FakeArqPool()
    assert await tasks.enqueue_event(pool, 42) is True
    assert pool.calls == [("process_event", (42,), "raw:42")]
    # id เดิมยังอยู่ในคิว -> arq ไม่รับ
    assert await tasks.enqueue_event(pool, 42) is False
    pool.finish("raw:42")
    assert await tasks.enqueue_event(pool, 42) is True


def test_job_name_matches_function() -> None:
    assert tasks.process_event.__qualname__ == tasks.PROCESS_EVENT_JOB
