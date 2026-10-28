"""tests/worker/test_cron.py — งานตามเวลา (fake ทั้งหมด + คิว procrastinate บน InMemoryConnector)"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.pipeline import replies
from app.pipeline.schemas import AiStepRecord, ConversationStatus, HandoffEndReason, ProcessStatus
from app.worker import cron
from app.worker.tasks import user_lock_for
from tests.fakes import DEFAULT_NOW, make_deps
from tests.infra_fakes import finish_all, make_queue, queued


async def _human_conv(deps: Any, line_user_id: str = "U1") -> Any:
    user = await deps.users.get_or_create(line_user_id)
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    conv = await deps.conversations.start_handoff(conv.id, "test")
    assert conv is not None
    return conv


class _NoTokens:
    async def delete_expired(self, limit: int = 0) -> int:
        return 0


async def _retention(deps: Any, raw: Any) -> cron.RetentionResult:
    return await cron.retention(deps, raw, _NoTokens())


async def test_handoff_timeout_sends_once_even_if_run_twice() -> None:
    deps = make_deps()
    conv = await _human_conv(deps)
    deps.clock.advance(hours=6)

    assert await cron.handoff_timeout(deps) == 1
    assert await cron.handoff_timeout(deps) == 0

    pushes = deps.line.pushes
    assert len(pushes) == 1
    line_user_id, messages, retry_key = pushes[0]
    assert line_user_id == "U1"
    assert messages == [
        {"type": "text", "text": replies.HANDOFF_TIMED_OUT_PREFIX + " " + replies.EMPTY}
    ]
    assert retry_key
    now_conv = await deps.conversations.get(conv.id)
    assert now_conv.status is ConversationStatus.BOT
    assert now_conv.handoff_end_reason is HandoffEndReason.TIMEOUT
    assert len(deps.notifier.ended) == 1
    row = deps.messages.outbound_rows(conv.id)[0]
    assert row.dedupe_key == f"out:handoff_timeout:{conv.id}:{conv.handoff_started_at.isoformat()}"


async def test_handoff_timeout_skips_when_other_path_closed_first() -> None:
    deps = make_deps()
    conv = await _human_conv(deps)
    deps.clock.advance(hours=6)
    # ลูกค้ากด "คุยกับ AI" ก่อน cron (handoff_check) -> cron เห็นแถวเก่าแต่ end_handoff ได้ None
    timed_out = await deps.conversations.list_handoff_timed_out(deps.settings.handoff_ttl)
    await deps.conversations.end_handoff(conv.id, HandoffEndReason.USER_RESUMED, conv.handoff_started_at)

    class StaleRepo:
        def __getattr__(self, name: str) -> Any:
            return getattr(deps.conversations, name)

        async def list_handoff_timed_out(self, ttl: timedelta, limit: int = 100) -> list[Any]:
            return timed_out

    stale_deps = replace(deps, conversations=StaleRepo())
    assert await cron.handoff_timeout(stale_deps) == 0
    assert deps.line.pushes == []


async def test_handoff_timeout_not_yet() -> None:
    deps = make_deps()
    await _human_conv(deps)
    deps.clock.advance(hours=5, minutes=59)
    assert await cron.handoff_timeout(deps) == 0
    assert deps.line.pushes == []


async def test_requeue_pending_only_stale() -> None:
    deps = make_deps()
    old = await deps.raw_events.insert(
        webhook_event_id="a", line_user_id="U1", event_type="message", payload={}, occurred_at=DEFAULT_NOW
    )
    deps.clock.advance(minutes=3)
    fresh = await deps.raw_events.insert(
        webhook_event_id="b", line_user_id="U1", event_type="message", payload={}, occurred_at=DEFAULT_NOW
    )
    queue, connector = make_queue()
    assert await cron.requeue_pending(deps, queue) == 1
    # lock ต่อ user ของแถวนั้น (อ่านจาก raw_events.get)
    assert queued(connector) == [
        ("process_event", {"raw_event_id": old}, user_lock_for("U1"), f"raw:{old}")
    ]
    assert fresh not in [q[1]["raw_event_id"] for q in queued(connector)]
    # งานเดิมยังรอในคิว -> ไม่ใส่ซ้ำ แต่ยังนับว่าเจอ (สัญญาณแจ้งเตือน)
    assert await cron.requeue_pending(deps, queue) == 1
    assert len(queued(connector)) == 1


async def test_requeue_pending_event_without_user_has_no_lock() -> None:
    deps = make_deps()
    rid = await deps.raw_events.insert(
        webhook_event_id="g", line_user_id=None, event_type="message", payload={}, occurred_at=DEFAULT_NOW
    )
    deps.clock.advance(minutes=3)
    queue, connector = make_queue()
    assert await cron.requeue_pending(deps, queue) == 1
    assert queued(connector) == [("process_event", {"raw_event_id": rid}, None, f"raw:{rid}")]


async def test_requeue_pending_enqueue_failure_is_logged_not_raised() -> None:
    deps = make_deps()
    await deps.raw_events.insert(
        webhook_event_id="a", line_user_id="U1", event_type="message", payload={}, occurred_at=DEFAULT_NOW
    )
    deps.clock.advance(minutes=3)
    queue, _ = make_queue(fail=True)
    assert await cron.requeue_pending(deps, queue) == 1


async def test_release_stuck_after_lock_ttl() -> None:
    deps = make_deps()
    rid = await deps.raw_events.insert(
        webhook_event_id="a", line_user_id="U1", event_type="message", payload={}, occurred_at=DEFAULT_NOW
    )
    await deps.raw_events.claim(rid)
    queue, connector = make_queue()
    # ยังไม่เกินเพดานเวลาของงาน (TURN_DEADLINE_S + 60) + ช่องเผื่อ 30 วิ -> ไม่ปล่อย
    deps.clock.advance(seconds=deps.settings.turn_deadline_s + 60 + 30)
    assert await cron.release_stuck(deps, queue) == 0
    deps.clock.advance(seconds=1)
    assert await cron.release_stuck(deps, queue) == 1
    assert deps.raw_events.db.raw_events[rid].process_status is ProcessStatus.PENDING
    assert queued(connector) == [
        ("process_event", {"raw_event_id": rid}, user_lock_for("U1"), f"raw:{rid}")
    ]
    # งานเดิม (ที่ worker ตาย) จบไปแล้ว -> ใส่ id เดิมได้
    finish_all(connector)
    await deps.raw_events.claim(rid)
    deps.clock.advance(seconds=deps.settings.turn_deadline_s + 91)
    assert await cron.release_stuck(deps, queue) == 1
    assert len(queued(connector)) == 2


def test_stuck_after_is_job_timeout_plus_margin() -> None:
    assert cron.stuck_after(50) == timedelta(seconds=50 + 60 + 30)


async def test_retention_deletes_in_batches() -> None:
    deps = make_deps()

    class Repo:
        def __init__(self) -> None:
            self.calls: list[tuple[datetime, int]] = []
            self.results = [cron.RETENTION_BATCH, cron.RETENTION_BATCH, 3]

        async def delete_older_than(self, cutoff: datetime, limit: int = 0) -> int:
            self.calls.append((cutoff, limit))
            return self.results.pop(0)

    repo = Repo()
    result = await _retention(deps, repo)
    assert result.raw_deleted == 2 * cron.RETENTION_BATCH + 3
    assert len(repo.calls) == 3
    assert repo.calls[0] == (DEFAULT_NOW - timedelta(days=90), cron.RETENTION_BATCH)


async def test_handoff_timeout_deleted_user_closes_without_push() -> None:
    deps = make_deps()
    conv = await _human_conv(deps)
    db = deps.users.db
    db.users[conv.user_id] = db.users[conv.user_id].model_copy(update={"deleted_at": DEFAULT_NOW})
    deps.clock.advance(hours=6)
    assert await cron.handoff_timeout(deps) == 1
    assert deps.line.pushes == []


# --- retention: รูป / anonymize ---


class _NoRaw:
    async def delete_older_than(self, cutoff: datetime, limit: int = 0) -> int:
        return 0


async def _seed_history(deps: Any) -> dict[str, Any]:
    """ข้อความขาเข้า (มีรูป) + ขาออก + ai_steps หนึ่งชุด ที่เวลา clock ปัจจุบัน"""
    user = await deps.users.get_or_create("U1")
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    mid = await deps.messages.add_inbound(
        conversation_id=conv.id, raw_event_id=1, message_type="image", text_redacted="ดูรูปนี้"
    )
    key = f"images/{conv.id}/m-1"
    await deps.storage.put_image(key, b"x", "image/jpeg")
    aid = await deps.messages.add_attachment(
        message_id=mid, storage_key=key, content_type="image/jpeg", size_bytes=1
    )
    await deps.messages.reserve_outbound(
        conversation_id=conv.id,
        raw_event_id=1,
        dedupe_key="out:1:ai_answer",
        outbound=replies.outbound_text("ai_answer", "ตอบ"),
        text_redacted="ตอบ",
    )
    run = await deps.ai_runs.create_run(raw_event_id=1, conversation_id=conv.id)
    await deps.ai_runs.add_step(
        AiStepRecord(
            ai_run_id=run,
            raw_event_id=1,
            seq=1,
            step="call_ai",
            input={"t": "x"},
            output={"o": 1},
            state_before={"s": 0},
            state_after={"s": 1},
            latency_ms=5,
        )
    )
    return {"conv": conv, "key": key, "attachment_id": aid}


async def test_retention_deletes_expired_images_only() -> None:
    deps = make_deps()
    seed = await _seed_history(deps)
    db = deps.users.db

    deps.clock.advance(days=29)
    assert (await _retention(deps, _NoRaw())).images_deleted == 0

    deps.clock.advance(days=2)
    result = await _retention(deps, _NoRaw())
    assert result.images_deleted == 1 and result.images_failed == 0
    assert seed["key"] not in deps.storage.objects
    assert db.attachments[seed["attachment_id"]]["deleted_at"] == deps.clock.now()
    # ข้อความยังไม่ครบ 12 เดือน
    assert result.messages_anonymized == 0
    assert all(m.text_redacted for m in db.messages.values())

    # รอบที่สอง: ไม่มีอะไรเหลือ ไม่เรียก Storage ซ้ำ
    again = await _retention(deps, _NoRaw())
    assert again.images_deleted == 0
    assert deps.storage.deleted == [seed["key"]]


async def test_retention_image_storage_failure_keeps_row_for_next_run() -> None:
    deps = make_deps()
    seed = await _seed_history(deps)
    db = deps.users.db
    deps.storage.fail_delete.add(seed["key"])
    deps.clock.advance(days=31)

    result = await _retention(deps, _NoRaw())
    assert (result.images_deleted, result.images_failed) == (0, 1)
    assert db.attachments[seed["attachment_id"]]["deleted_at"] is None

    deps.storage.fail_delete.clear()
    result = await _retention(deps, _NoRaw())
    assert (result.images_deleted, result.images_failed) == (1, 0)
    assert db.attachments[seed["attachment_id"]]["deleted_at"] is not None


async def test_retention_anonymizes_after_trace_months_idempotent() -> None:
    deps = make_deps()
    await _seed_history(deps)
    db = deps.users.db

    # ยังไม่ครบ 12 เดือนตามปฏิทิน
    deps.clock.set(cron.months_before(DEFAULT_NOW, -12) - timedelta(seconds=1))
    first = await _retention(deps, _NoRaw())
    assert (first.messages_anonymized, first.ai_steps_anonymized) == (0, 0)

    deps.clock.advance(seconds=2)
    result = await _retention(deps, _NoRaw())
    assert result.messages_anonymized == 2
    assert result.ai_steps_anonymized == 1
    assert all(m.text_redacted is None and m.payload is None for m in db.messages.values())
    (step,) = db.ai_steps
    assert (step.input, step.output, step.state_before, step.state_after) == (None, None, None, None)
    # ค่าสถิติยังอยู่
    assert step.latency_ms == 5 and step.step == "call_ai"

    again = await _retention(deps, _NoRaw())
    assert (again.messages_anonymized, again.ai_steps_anonymized) == (0, 0)


async def test_retention_part_failure_does_not_stop_others() -> None:
    deps = make_deps()
    await _seed_history(deps)

    class BrokenRaw:
        async def delete_older_than(self, cutoff: datetime, limit: int = 0) -> int:
            raise RuntimeError("db hiccup")

    deps.clock.set(cron.months_before(DEFAULT_NOW, -13))
    result = await _retention(deps, BrokenRaw())
    assert result.raw_deleted == 0
    assert result.images_deleted == 1
    assert result.messages_anonymized == 2


@pytest.mark.parametrize(
    ("now", "months", "expected"),
    [
        (datetime(2026, 10, 22, 3, 0, tzinfo=UTC), 12, datetime(2025, 10, 22, 3, 0, tzinfo=UTC)),
        (datetime(2026, 3, 31, 1, 2, 3, tzinfo=UTC), 1, datetime(2026, 2, 28, 1, 2, 3, tzinfo=UTC)),
        (datetime(2024, 2, 29, tzinfo=UTC), 12, datetime(2023, 2, 28, tzinfo=UTC)),
        (datetime(2026, 1, 15, tzinfo=UTC), 1, datetime(2025, 12, 15, tzinfo=UTC)),
    ],
)
def test_months_before(now: datetime, months: int, expected: datetime) -> None:
    assert cron.months_before(now, months) == expected


# --- reply tokens / งานค้างของ worker ที่ตาย (D-010) ---


async def test_retention_deletes_expired_reply_tokens_in_batches() -> None:
    deps = make_deps()

    class Tokens:
        def __init__(self) -> None:
            self.results = [cron.RETENTION_BATCH, 7]
            self.limits: list[int] = []

        async def delete_expired(self, limit: int = 0) -> int:
            self.limits.append(limit)
            return self.results.pop(0)

    tokens = Tokens()
    result = await cron.retention(deps, _NoRaw(), tokens)
    assert result.reply_tokens_deleted == cron.RETENTION_BATCH + 7
    assert tokens.limits == [cron.RETENTION_BATCH, cron.RETENTION_BATCH]


async def test_retention_reply_token_failure_does_not_stop_others() -> None:
    deps = make_deps()
    await _seed_history(deps)

    class BrokenTokens:
        async def delete_expired(self, limit: int = 0) -> int:
            raise RuntimeError("db hiccup")

    deps.clock.set(cron.months_before(DEFAULT_NOW, -13))
    result = await cron.retention(deps, _NoRaw(), BrokenTokens())
    assert result.reply_tokens_deleted == 0
    assert result.images_deleted == 1 and result.messages_anonymized == 2


async def test_retry_stalled_jobs_only_dead_workers() -> None:
    from datetime import UTC
    from datetime import datetime as dt

    queue, connector = make_queue()
    jm = queue.job_manager
    await queue.configure_task("process_event", lock="user:a").defer_async(raw_event_id=1)
    await queue.configure_task("process_event", lock="user:b").defer_async(raw_event_id=2)
    dead = await jm.register_worker()
    alive = await jm.register_worker()
    job_dead = await jm.fetch_job(queues=None, worker_id=dead)
    job_alive = await jm.fetch_job(queues=None, worker_id=alive)
    assert job_dead is not None and job_alive is not None

    # worker ที่ยังส่ง heartbeat -> ไม่แตะ
    assert await cron.retry_stalled_jobs(jm) == 0
    # dead หยุดส่ง heartbeat นานกว่า STALLED_HEARTBEAT_S -> งานกลับเป็น todo (ปล่อย lock ของ user)
    connector.workers[dead] = dt.now(UTC) - timedelta(seconds=cron.STALLED_HEARTBEAT_S + 5)
    assert await cron.retry_stalled_jobs(jm) == 1
    assert connector.jobs[job_dead.id]["status"] == "todo"
    assert connector.jobs[job_alive.id]["status"] == "doing"


class _RetryConflictJobManager:
    """job manager ที่ retry_job ชน unique constraint (InMemoryConnector ไม่เช็ค queueing_lock ตอน retry)"""

    def __init__(self, jm: Any, constraint_name: str) -> None:
        self._jm = jm
        self._constraint_name = constraint_name

    async def get_stalled_jobs(self, **kwargs: Any) -> Any:
        return await self._jm.get_stalled_jobs(**kwargs)

    async def retry_job(self, job: Any) -> None:
        from procrastinate.exceptions import UniqueViolation

        raise UniqueViolation(constraint_name=self._constraint_name, queueing_lock="raw:1")

    async def finish_job(self, job: Any, status: Any, delete_job: bool) -> None:
        await self._jm.finish_job(job, status=status, delete_job=delete_job)


async def _stalled_job(queue: Any, connector: Any) -> Any:
    jm = queue.job_manager
    await queue.configure_task("process_event", lock="user:a").defer_async(raw_event_id=1)
    dead = await jm.register_worker()
    job = await jm.fetch_job(queues=None, worker_id=dead)
    assert job is not None
    connector.workers[dead] = datetime.now(UTC) - timedelta(seconds=cron.STALLED_HEARTBEAT_S + 5)
    return job


async def test_retry_stalled_jobs_closes_job_superseded_by_requeue() -> None:
    from procrastinate.manager import QUEUEING_LOCK_CONSTRAINT

    queue, connector = make_queue()
    job = await _stalled_job(queue, connector)
    jm = _RetryConflictJobManager(queue.job_manager, QUEUEING_LOCK_CONSTRAINT)

    # มีงาน todo ของ raw event เดียวกันแล้ว -> retry ไม่ได้ ปิดงานเดิมเป็น failed เพื่อปล่อย lock ของ user
    assert await cron.retry_stalled_jobs(jm) == 1
    assert connector.jobs[job.id]["status"] == "failed"


async def test_retry_stalled_jobs_leaves_job_on_other_unique_violation() -> None:
    queue, connector = make_queue()
    job = await _stalled_job(queue, connector)
    jm = _RetryConflictJobManager(queue.job_manager, "some_other_constraint")

    assert await cron.retry_stalled_jobs(jm) == 0
    assert connector.jobs[job.id]["status"] == "doing"
