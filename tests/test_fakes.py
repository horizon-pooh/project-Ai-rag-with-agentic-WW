"""tests/test_fakes.py — fake ใน tests/fakes.py ต้องทำตามสัญญาของ app/pipeline/ports.py (โดยเฉพาะ UPDATE แบบมีเงื่อนไข)"""

from __future__ import annotations

from dataclasses import fields
from datetime import timedelta

import pytest

from app.pipeline import ports
from app.pipeline.schemas import (
    ConversationStatus,
    Counters,
    HandoffEndReason,
    OutboundMessage,
    OutboundStatus,
    ProcessStatus,
)
from tests.fakes import FakeClock, make_deps


@pytest.fixture
def deps() -> ports.Deps:
    return make_deps()


def test_every_dep_implements_its_port(deps: ports.Deps) -> None:
    for f in fields(deps):
        if f.name == "settings":
            continue
        proto = getattr(ports, f.type) if isinstance(f.type, str) else f.type
        assert isinstance(getattr(deps, f.name), proto), f.name


def test_make_deps_rejects_unknown_override() -> None:
    with pytest.raises(TypeError):
        make_deps(nope=1)


async def _human_conv(deps: ports.Deps):
    user = await deps.users.get_or_create("U1")
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    started = await deps.conversations.start_handoff(conv.id, "rule_escalate")
    assert started is not None
    return started


# --- raw events ---


async def test_insert_dedupes_and_rejects_reply_token(deps: ports.Deps, ) -> None:
    now = deps.clock.now()
    kw = dict(webhook_event_id="wh1", line_user_id="U1", event_type="message", payload={}, occurred_at=now)
    rid = await deps.raw_events.insert(**kw)
    assert rid is not None
    assert await deps.raw_events.insert(**kw) is None
    with pytest.raises(ValueError):
        await deps.raw_events.insert(**{**kw, "webhook_event_id": "wh2", "payload": {"replyToken": "x"}})


async def test_claim_twice_second_is_none(deps: ports.Deps) -> None:
    rid = await deps.raw_events.insert(
        webhook_event_id="wh1", line_user_id="U1", event_type="message", payload={},
        occurred_at=deps.clock.now(),
    )
    first = await deps.raw_events.claim(rid)
    assert first is not None and first.process_status is ProcessStatus.PROCESSING and first.attempts == 1
    assert await deps.raw_events.claim(rid) is None
    await deps.raw_events.mark_done(rid)
    assert await deps.raw_events.claim(rid) is None
    assert (await deps.raw_events.get(rid)).process_status is ProcessStatus.DONE


async def test_release_and_release_stuck_allow_reclaim(deps: ports.Deps) -> None:
    clock: FakeClock = deps.clock
    rid = await deps.raw_events.insert(
        webhook_event_id="wh1", line_user_id="U1", event_type="message", payload={},
        occurred_at=clock.now(),
    )
    await deps.raw_events.claim(rid)
    await deps.raw_events.release(rid, "temporary")
    assert (await deps.raw_events.claim(rid)).attempts == 2
    assert await deps.raw_events.release_stuck(clock.now() - timedelta(minutes=5)) == []
    clock.advance(minutes=10)
    assert await deps.raw_events.release_stuck(clock.now() - timedelta(minutes=5)) == [rid]
    assert await deps.raw_events.claim(rid) is not None


async def test_mark_done_rejects_non_final_status(deps: ports.Deps) -> None:
    with pytest.raises(ValueError):
        await deps.raw_events.mark_done(1, ProcessStatus.PENDING)


async def test_pending_ids_for_user_oldest_first(deps: ports.Deps) -> None:
    t = deps.clock.now()
    later = await deps.raw_events.insert(
        webhook_event_id="b", line_user_id="U1", event_type="message", payload={}, occurred_at=t
    )
    earlier = await deps.raw_events.insert(
        webhook_event_id="a", line_user_id="U1", event_type="message", payload={},
        occurred_at=t - timedelta(seconds=1),
    )
    await deps.raw_events.insert(
        webhook_event_id="c", line_user_id="U2", event_type="message", payload={}, occurred_at=t
    )
    assert await deps.raw_events.list_pending_ids_for_user("U1") == [earlier, later]


# --- handoff ---


async def test_start_handoff_only_from_bot(deps: ports.Deps) -> None:
    conv = await _human_conv(deps)
    assert conv.status is ConversationStatus.HUMAN
    assert conv.handoff_started_at == deps.clock.now()
    assert await deps.conversations.start_handoff(conv.id, "again") is None


async def test_double_end_handoff_second_is_none(deps: ports.Deps) -> None:
    conv = await _human_conv(deps)
    await deps.state.save(conv.id, state={"cat": "fridge"}, counters=Counters(), expires_at=deps.clock.now())
    ended = await deps.conversations.end_handoff(
        conv.id, HandoffEndReason.USER_RESUMED, conv.handoff_started_at
    )
    assert ended is not None
    assert ended.status is ConversationStatus.BOT
    assert ended.handoff_end_reason is HandoffEndReason.USER_RESUMED
    assert await deps.state.get(conv.id) is None  # ล้าง state ใน transaction เดียวกัน
    assert (
        await deps.conversations.end_handoff(conv.id, HandoffEndReason.TIMEOUT, conv.handoff_started_at)
        is None
    )


async def test_end_handoff_with_stale_started_at_is_none(deps: ports.Deps) -> None:
    clock: FakeClock = deps.clock
    first = await _human_conv(deps)
    await deps.conversations.end_handoff(first.id, HandoffEndReason.TIMEOUT, first.handoff_started_at)
    clock.advance(minutes=30)
    second = await deps.conversations.start_handoff(first.id, "again")
    assert second is not None and second.handoff_started_at != first.handoff_started_at
    # คำสั่งที่อ้างรอบเก่า (เช่น ลิงก์ /close เก่า) ต้องไม่ปิดรอบใหม่
    assert (
        await deps.conversations.end_handoff(first.id, HandoffEndReason.STAFF_CLOSED, first.handoff_started_at)
        is None
    )
    assert (await deps.conversations.get(first.id)).status is ConversationStatus.HUMAN


async def test_list_handoff_timed_out(deps: ports.Deps) -> None:
    clock: FakeClock = deps.clock
    conv = await _human_conv(deps)
    ttl = deps.settings.handoff_ttl
    clock.advance(ttl - timedelta(seconds=1))
    assert await deps.conversations.list_handoff_timed_out(ttl) == []
    clock.advance(seconds=1)
    assert [c.id for c in await deps.conversations.list_handoff_timed_out(ttl)] == [conv.id]


async def test_list_open_human_newest_first(deps: ports.Deps) -> None:
    clock: FakeClock = deps.clock
    a = await _human_conv(deps)
    clock.advance(seconds=5)
    user = await deps.users.get_or_create("U2")
    b, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    await deps.conversations.start_handoff(b.id, "x")
    other = await deps.users.get_or_create("U3")
    await deps.conversations.get_or_open(other.id, deps.settings.state_ttl)
    assert [c.id for c in await deps.conversations.list_open_human()] == [b.id, a.id]
    assert [c.id for c in await deps.conversations.list_open_human(limit=1)] == [b.id]


async def test_close_token_single_use_and_expiry(deps: ports.Deps) -> None:
    clock: FakeClock = deps.clock
    conv = await _human_conv(deps)
    ttl = deps.settings.close_token_ttl
    token = await deps.conversations.create_close_token(conv.id, conv.handoff_started_at, ttl)
    db = deps.conversations.db
    assert token not in db.close_tokens  # เก็บแค่ hash
    got = await deps.conversations.consume_close_token(token)
    assert got is not None and got.conversation_id == conv.id
    assert got.handoff_started_at == conv.handoff_started_at
    assert await deps.conversations.consume_close_token(token) is None
    expired = await deps.conversations.create_close_token(conv.id, conv.handoff_started_at, ttl)
    clock.advance(ttl)
    assert await deps.conversations.consume_close_token(expired) is None
    assert await deps.conversations.consume_close_token("bogus") is None


# --- conversations: เปิดใหม่เมื่อเงียบ ---


async def test_get_or_open_reopens_idle_bot_but_never_human(deps: ports.Deps) -> None:
    clock: FakeClock = deps.clock
    ttl = deps.settings.state_ttl
    user = await deps.users.get_or_create("U1")
    conv, reopened = await deps.conversations.get_or_open(user.id, ttl)
    assert not reopened
    clock.advance(ttl - timedelta(seconds=1))
    assert (await deps.conversations.get_or_open(user.id, ttl)) == (conv, False)
    clock.advance(seconds=1)
    new, reopened = await deps.conversations.get_or_open(user.id, ttl)
    assert reopened and new.id != conv.id
    assert (await deps.conversations.get(conv.id)).closed_at is not None

    await deps.conversations.start_handoff(new.id, "x")
    clock.advance(hours=10)
    same, reopened = await deps.conversations.get_or_open(user.id, ttl)
    assert same.id == new.id and not reopened


# --- outbound ---


async def test_reserve_outbound_twice(deps: ports.Deps) -> None:
    user = await deps.users.get_or_create("U1")
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    out = OutboundMessage(messages=[{"type": "text", "text": "hi"}], purpose="ai_answer")
    kw = dict(conversation_id=conv.id, raw_event_id=1, dedupe_key="out:1:ai_answer", outbound=out)

    first = await deps.messages.reserve_outbound(**kw)
    assert not first.already_sent
    # ยังไม่ได้ส่ง (เช่น พังกลางทาง) -> แถวเดิม ส่งใหม่ได้
    again = await deps.messages.reserve_outbound(**kw)
    assert again.message_id == first.message_id and not again.already_sent

    await deps.messages.mark_outbound(first.message_id, OutboundStatus.SENT)
    after_sent = await deps.messages.reserve_outbound(**kw)
    assert after_sent.message_id == first.message_id and after_sent.already_sent
    assert len(deps.messages.outbound_rows(conv.id)) == 1


async def test_add_inbound_idempotent_and_recent_texts(deps: ports.Deps) -> None:
    user = await deps.users.get_or_create("U1")
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    m1 = await deps.messages.add_inbound(conversation_id=conv.id, raw_event_id=1, message_type="text", text_redacted="ตู้เย็น")
    assert await deps.messages.add_inbound(conversation_id=conv.id, raw_event_id=1, message_type="text", text_redacted="ตู้เย็น") == m1
    await deps.messages.add_inbound(conversation_id=conv.id, raw_event_id=2, message_type="text", text_redacted="สีเขียว")
    turns = await deps.messages.recent_texts_redacted(conv.id, exclude_raw_event_id=2)
    assert [t.text_redacted for t in turns] == ["ตู้เย็น"]
    assert await deps.messages.count_inbound_since(conv.id, deps.clock.now()) == 2


async def test_reply_tokens_getdel_and_ttl(deps: ports.Deps) -> None:
    clock: FakeClock = deps.clock
    await deps.reply_tokens.put(1, "tok")
    assert await deps.reply_tokens.take(1) == "tok"
    assert await deps.reply_tokens.take(1) is None
    await deps.reply_tokens.put(2, "tok2")
    clock.advance(seconds=deps.settings.reply_token_ttl_s)
    assert await deps.reply_tokens.take(2) is None


async def test_create_run_idempotent(deps: ports.Deps) -> None:
    user = await deps.users.get_or_create("U1")
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    a = await deps.ai_runs.create_run(raw_event_id=7, conversation_id=conv.id)
    assert await deps.ai_runs.create_run(raw_event_id=7, conversation_id=conv.id) == a
