"""tests/worker/test_cron.py — งานตามเวลา (fake ทั้งหมด)"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from app.pipeline import replies
from app.pipeline.schemas import ConversationStatus, HandoffEndReason, ProcessStatus
from app.worker import cron
from tests.fakes import DEFAULT_NOW, make_deps
from tests.infra_fakes import FakeArqPool, FakeUserLookup


async def _human_conv(deps: Any, line_user_id: str = "U1") -> Any:
    user = await deps.users.get_or_create(line_user_id)
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    conv = await deps.conversations.start_handoff(conv.id, "test")
    assert conv is not None
    return conv


def _ctx(deps: Any, **extra: Any) -> dict[str, Any]:
    return {"deps": deps, "redis": FakeArqPool(), **extra}


async def test_handoff_timeout_sends_once_even_if_run_twice() -> None:
    deps = make_deps()
    conv = await _human_conv(deps)
    deps.clock.advance(hours=6)
    ctx = _ctx(deps, user_lookup=FakeUserLookup({conv.id: "U1"}))

    assert await cron.handoff_timeout(ctx) == 1
    assert await cron.handoff_timeout(ctx) == 0

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
    ctx = _ctx(stale_deps, user_lookup=FakeUserLookup({conv.id: "U1"}))
    assert await cron.handoff_timeout(ctx) == 0
    assert deps.line.pushes == []


async def test_handoff_timeout_not_yet() -> None:
    deps = make_deps()
    conv = await _human_conv(deps)
    deps.clock.advance(hours=5, minutes=59)
    ctx = _ctx(deps, user_lookup=FakeUserLookup({conv.id: "U1"}))
    assert await cron.handoff_timeout(ctx) == 0
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
    ctx = _ctx(deps)
    assert await cron.requeue_pending(ctx) == 1
    assert ctx["redis"].calls == [("process_event", (old,), f"raw:{old}")]
    assert fresh not in [c[1][0] for c in ctx["redis"].calls]


async def test_release_stuck_after_lock_ttl() -> None:
    deps = make_deps()
    rid = await deps.raw_events.insert(
        webhook_event_id="a", line_user_id="U1", event_type="message", payload={}, occurred_at=DEFAULT_NOW
    )
    await deps.raw_events.claim(rid)
    ctx = _ctx(deps)
    # ยังไม่เกิน TTL ล็อก + ช่องเผื่อ -> ไม่ปล่อย
    deps.clock.advance(seconds=deps.settings.turn_deadline_s + 30)
    assert await cron.release_stuck(ctx) == 0
    deps.clock.advance(seconds=61)
    assert await cron.release_stuck(ctx) == 1
    assert deps.raw_events.db.raw_events[rid].process_status is ProcessStatus.PENDING
    assert ctx["redis"].calls == [("process_event", (rid,), f"raw:{rid}")]


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
    total = await cron.retention(_ctx(deps, raw_retention=repo))
    assert total == 2 * cron.RETENTION_BATCH + 3
    assert len(repo.calls) == 3
    assert repo.calls[0] == (DEFAULT_NOW - timedelta(days=90), cron.RETENTION_BATCH)
