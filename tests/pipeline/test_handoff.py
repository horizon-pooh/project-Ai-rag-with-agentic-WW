"""tests/pipeline/test_handoff.py

เคสทดสอบของ app/pipeline/handoff.py

รายละเอียด:
 - start_handoff สำเร็จ -> human + แจ้ง call center ครั้งเดียว พร้อมลิงก์ /close ที่ใช้ได้กับรอบนี้
 - ไม่มี PUBLIC_BASE_URL -> close_url None (ไม่สร้าง token)
 - เป็น human อยู่แล้ว (แข่งกัน / fast path) -> None ไม่แจ้งซ้ำ
 - แจ้งไม่สำเร็จ -> การส่งต่อยังอยู่
 - end_handoff สำเร็จ -> bot + ล้าง state + แจ้งครั้งเดียว; แข่งกัน -> None ไม่แจ้ง; ลิงก์รอบเก่าปิดรอบใหม่ไม่ได้
 - in_business_hours: ไม่ตั้ง -> True, ขอบเริ่ม/เลิก, ข้ามเที่ยงคืน, timezone

แนวคิดจาก: ใหม่ (docs/Line_ai_bot.md หัวข้อ 5)
สถานะ: มีโค้ดแล้ว (งาน 4)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.pipeline.handoff import end_handoff, in_business_hours, start_handoff
from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    Conversation,
    ConversationStatus,
    Counters,
    HandoffEndReason,
)
from tests.fakes import FakeClock, FakeNotifier, make_deps, make_settings


class BrokenNotifier(FakeNotifier):
    async def handoff_started(self, conv, summary, close_url):
        raise RuntimeError("notify down")

    async def handoff_ended(self, conv, reason):
        raise RuntimeError("notify down")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def deps(clock: FakeClock) -> Deps:
    return make_deps(clock=clock)


async def _bot_conv(deps: Deps) -> Conversation:
    user = await deps.users.get_or_create("U-test")
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    return conv


def _token_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


# --- start_handoff ---


async def test_start_handoff_sets_human_and_notifies_once_with_close_url(deps, clock):
    conv = await _bot_conv(deps)
    new = await start_handoff(deps, conv, "rule:เคลม", summary="ลูกค้าขอเคลม")
    assert new is not None
    assert new.status is ConversationStatus.HUMAN
    assert new.handoff_started_at == clock.now()
    assert new.handoff_reason == "rule:เคลม"

    started = deps.notifier.started
    assert len(started) == 1
    notified_conv, summary, url = started[0]
    assert notified_conv == new and summary == "ลูกค้าขอเคลม"
    assert url is not None and url.startswith("https://bot.example.test/close?token=")
    # token ในลิงก์ใช้ปิดรอบนี้ได้ครั้งเดียว
    closed = await deps.conversations.consume_close_token(_token_from(url))
    assert closed is not None
    assert closed.conversation_id == new.id
    assert closed.handoff_started_at == new.handoff_started_at
    assert await deps.conversations.consume_close_token(_token_from(url)) is None


async def test_start_handoff_close_token_expires_with_ttl(clock):
    deps = make_deps(clock=clock, settings=make_settings(close_token_ttl_hours=1))
    conv = await _bot_conv(deps)
    await start_handoff(deps, conv, "x", summary="s")
    url = deps.notifier.started[0][2]
    clock.advance(hours=1)
    assert await deps.conversations.consume_close_token(_token_from(url)) is None


async def test_start_handoff_without_public_base_url_close_url_none(clock):
    deps = make_deps(clock=clock, settings=make_settings(public_base_url=None))
    conv = await _bot_conv(deps)
    new = await start_handoff(deps, conv, "x", summary="s")
    assert new is not None
    assert deps.notifier.started[0][2] is None
    assert deps.conversations.db.close_tokens == {}


async def test_start_handoff_race_returns_none_and_does_not_notify(deps):
    conv = await _bot_conv(deps)
    assert await start_handoff(deps, conv, "fast_path", summary="s") is not None
    # งานที่สองถือ conv เก่า (bot)
    assert await start_handoff(deps, conv, "rule:เคลม", summary="s") is None
    assert len(deps.notifier.started) == 1
    assert len(deps.conversations.db.close_tokens) == 1
    current = await deps.conversations.get(conv.id)
    assert current.handoff_reason == "fast_path"


async def test_start_handoff_notifier_failure_keeps_handoff(clock):
    deps = make_deps(clock=clock, notifier=BrokenNotifier())
    conv = await _bot_conv(deps)
    new = await start_handoff(deps, conv, "x", summary="s")
    assert new is not None
    assert (await deps.conversations.get(conv.id)).status is ConversationStatus.HUMAN


# --- end_handoff ---


async def test_end_handoff_success_clears_state_and_notifies_once(deps, clock):
    conv = await start_handoff(deps, await _bot_conv(deps), "x", summary="s")
    await deps.state.save(
        conv.id, state={"a": 1}, counters=Counters(), expires_at=clock.now() + timedelta(hours=2)
    )
    clock.advance(minutes=10)
    new = await end_handoff(deps, conv, HandoffEndReason.USER_RESUMED)
    assert new is not None
    assert new.status is ConversationStatus.BOT
    assert new.handoff_ended_at == clock.now()
    assert new.handoff_end_reason is HandoffEndReason.USER_RESUMED
    assert await deps.state.get(conv.id) is None
    assert deps.notifier.ended == [(new, HandoffEndReason.USER_RESUMED)]


async def test_end_handoff_race_second_caller_gets_none_no_notify(deps):
    conv = await start_handoff(deps, await _bot_conv(deps), "x", summary="s")
    assert await end_handoff(deps, conv, HandoffEndReason.TIMEOUT) is not None  # cron
    assert await end_handoff(deps, conv, HandoffEndReason.STAFF_CLOSED) is None  # /close
    assert [r for _, r in deps.notifier.ended] == [HandoffEndReason.TIMEOUT]


async def test_end_handoff_old_round_cannot_close_new_round(deps, clock):
    old = await start_handoff(deps, await _bot_conv(deps), "x", summary="s")
    assert await end_handoff(deps, old, HandoffEndReason.USER_RESUMED) is not None
    clock.advance(minutes=1)
    new_round = await start_handoff(deps, old, "y", summary="s")
    assert new_round is not None
    # ลิงก์ /close ของรอบเก่า (expected_started_at เก่า) ปิดรอบใหม่ไม่ได้
    assert await end_handoff(deps, old, HandoffEndReason.STAFF_CLOSED) is None
    assert (await deps.conversations.get(old.id)).status is ConversationStatus.HUMAN
    assert len(deps.notifier.ended) == 1


async def test_end_handoff_on_bot_never_handed_off_returns_none(deps):
    conv = await _bot_conv(deps)
    assert await end_handoff(deps, conv, HandoffEndReason.TIMEOUT) is None
    assert deps.notifier.ended == []


async def test_end_handoff_notifier_failure_keeps_bot(clock):
    deps = make_deps(clock=clock, notifier=BrokenNotifier())
    conv = await start_handoff(deps, await _bot_conv(deps), "x", summary="s")
    assert await end_handoff(deps, conv, HandoffEndReason.TIMEOUT) is not None
    assert (await deps.conversations.get(conv.id)).status is ConversationStatus.BOT


# --- in_business_hours ---


def _bkk(hour: int, minute: int = 0) -> datetime:
    """เวลาไทย (UTC+7) -> UTC"""
    return datetime(2026, 10, 22, hour, minute, tzinfo=UTC) - timedelta(hours=7)


def test_in_business_hours_unset_is_true():
    assert in_business_hours(_bkk(3), make_settings(callcenter_hours=None)) is True


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(8, 59, False), (9, 0, True), (12, 0, True), (17, 59, True), (18, 0, False), (23, 0, False)],
)
def test_in_business_hours_boundaries_bangkok(hour, minute, expected):
    s = make_settings(callcenter_hours="09:00-18:00")
    assert in_business_hours(_bkk(hour, minute), s) is expected


def test_in_business_hours_uses_settings_timezone_not_utc():
    s = make_settings(callcenter_hours="09:00-18:00")
    # 03:00 UTC = 10:00 ไทย -> ในเวลา (ถ้าเทียบเป็น UTC จะผิดเป็นนอกเวลา)
    assert in_business_hours(datetime(2026, 10, 22, 3, 0, tzinfo=UTC), s) is True
    # 12:00 UTC = 19:00 ไทย -> นอกเวลา (ถ้าเทียบเป็น UTC จะผิดเป็นในเวลา)
    assert in_business_hours(datetime(2026, 10, 22, 12, 0, tzinfo=UTC), s) is False
    s_utc = make_settings(callcenter_hours="09:00-18:00", timezone="UTC")
    assert in_business_hours(datetime(2026, 10, 22, 12, 0, tzinfo=UTC), s_utc) is True


@pytest.mark.parametrize(("hour", "expected"), [(21, False), (22, True), (2, True), (6, False)])
def test_in_business_hours_overnight(hour, expected):
    s = make_settings(callcenter_hours="22:00-06:00")
    assert in_business_hours(_bkk(hour), s) is expected


def test_in_business_hours_naive_treated_as_utc():
    s = make_settings(callcenter_hours="09:00-18:00")
    assert in_business_hours(datetime(2026, 10, 22, 3, 0), s) is True
