"""tests/pipeline/test_handoff_check.py

เคสทดสอบของ app/pipeline/steps/handoff_check.py

รายละเอียด (สถานะ / ข้อความเข้า -> ผลที่คาด):
 - human, เกิน 6 ชม., ข้อความทั่วไป -> end_handoff(timeout) -> CONTINUE + timed_out
 - human, postback action=resume_ai -> end_handoff(user_resumed) -> RESUMED
 - human, พิมพ์ "คุยกับ AI" / " คุย กับ ai " / "คุยกับAI" (มี zero-width) -> RESUMED
 - human, พิมพ์ "คุยกับ AI หน่อย หาตู้เย็น" -> SILENT (ไม่นับเป็นคำสั่ง ตั้งใจ)
 - bot, postback action=resume_ai หรือพิมพ์ "คุยกับ AI" -> ALREADY_BOT
 - bot, postback action=talk_to_staff -> ESCALATE
 - human, postback action=talk_to_staff -> SILENT
 - human, ข้อความทั่วไป / รูป -> SILENT (บันทึกแต่ไม่ตอบ, รูปไม่ส่ง AI)
 - bot, ข้อความทั่วไป -> CONTINUE
 - human, เกิน 6 ชม. + พิมพ์ "คุยกับ AI" -> timeout ชนะก่อน แล้ว ALREADY_BOT (ไม่ส่งข้อความซ้ำ)
 - end_handoff แข่งกัน (cron timeout กับลูกค้ากดคืน / /close) -> UPDATE มีเงื่อนไข สำเร็จทางเดียว แจ้งพนักงานครั้งเดียว
 - end_handoff สำเร็จ -> conversation_state ถูกล้าง
 - ข้อความระหว่าง human ไม่ต่ออายุ state และไม่เลื่อน handoff_started_at
 - fast_path ส่งต่อไปแล้ว แล้ว event เดิมมาถึง -> ไม่ส่งต่อซ้ำ

แนวคิดจาก: test/unit/README.md (แบบเก่า), เคสใหม่จาก docs/Line_ai_bot.md หัวข้อ การส่งต่อพนักงานและการคืน AI
สถานะ: มีโค้ดแล้ว (งาน 4) — ใช้รายการคำสั่งชั่วคราวในเทสต์ ไม่อ่าน prompts/rules จริง
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.pipeline import handoff
from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    Conversation,
    ConversationStatus,
    Counters,
    HandoffEndReason,
    HandoffOutcome,
)
from app.pipeline.steps.handoff_check import handoff_check, load_resume_phrases
from app.pipeline.text import normalize
from tests.fakes import FakeClock, make_deps, make_event

RESUME = frozenset({normalize("คุยกับ AI"), normalize("กลับไปคุยกับบอท")})
ZWSP = "​"


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


async def _human_conv(deps: Deps) -> Conversation:
    conv = await _bot_conv(deps)
    new = await deps.conversations.start_handoff(conv.id, "test")
    assert new is not None
    return new


def _ended(deps: Deps) -> list[tuple[Conversation, HandoffEndReason]]:
    return deps.notifier.ended  # type: ignore[attr-defined]


def _sent(deps: Deps) -> list:
    return deps.line.sent  # type: ignore[attr-defined]


async def _check(deps: Deps, conv: Conversation, **event_kw):
    return await handoff_check(deps, make_event(**event_kw), conv, resume_phrases=RESUME)


# --- ข้อ 1: หมดเวลา ---


async def test_human_timed_out_plain_text_continue_with_timed_out(deps, clock):
    conv = await _human_conv(deps)
    clock.advance(hours=6)  # ครบ 6 ชม. พอดี (>=)
    result, new = await _check(deps, conv, text="หาตู้เย็น")
    assert result.outcome is HandoffOutcome.CONTINUE
    assert result.timed_out is True
    assert new.status is ConversationStatus.BOT
    assert new.handoff_end_reason is HandoffEndReason.TIMEOUT
    assert [r for _, r in _ended(deps)] == [HandoffEndReason.TIMEOUT]


async def test_human_just_before_timeout_stays_silent(deps, clock):
    conv = await _human_conv(deps)
    clock.advance(hours=6, seconds=-1)
    result, new = await _check(deps, conv, text="หาตู้เย็น")
    assert result.outcome is HandoffOutcome.SILENT
    assert result.timed_out is False
    assert new.status is ConversationStatus.HUMAN
    assert _ended(deps) == []


async def test_timeout_then_resume_command_already_bot_single_end(deps, clock):
    conv = await _human_conv(deps)
    clock.advance(hours=7)
    result, new = await _check(deps, conv, text="คุยกับ AI")
    assert result.outcome is HandoffOutcome.ALREADY_BOT
    assert result.timed_out is True
    assert new.handoff_end_reason is HandoffEndReason.TIMEOUT
    # จบรอบครั้งเดียว (timeout) ไม่มี user_resumed ซ้ำ และ handoff_check ไม่ส่งข้อความเอง
    assert [r for _, r in _ended(deps)] == [HandoffEndReason.TIMEOUT]
    assert _sent(deps) == []


async def test_timeout_then_talk_to_staff_escalates(deps, clock):
    conv = await _human_conv(deps)
    clock.advance(hours=7)
    result, new = await _check(deps, conv, postback="action=talk_to_staff")
    assert result.outcome is HandoffOutcome.ESCALATE
    assert result.timed_out is True
    assert new.status is ConversationStatus.BOT


# --- ข้อ 2: คำสั่งคืน AI ---


async def test_human_postback_resume_ai_resumed(deps):
    conv = await _human_conv(deps)
    result, new = await _check(deps, conv, postback="action=resume_ai")
    assert result.outcome is HandoffOutcome.RESUMED
    assert result.timed_out is False
    assert new.status is ConversationStatus.BOT
    assert new.handoff_end_reason is HandoffEndReason.USER_RESUMED
    assert [r for _, r in _ended(deps)] == [HandoffEndReason.USER_RESUMED]


@pytest.mark.parametrize(
    "text",
    ["คุยกับ AI", " คุย กับ ai ", f"คุยกับ{ZWSP}AI", "คุย‍กับ﻿Ai", "คุยกับ AI\n"],
)
async def test_human_typed_resume_variants_resumed(deps, text):
    conv = await _human_conv(deps)
    result, new = await _check(deps, conv, text=text)
    assert result.outcome is HandoffOutcome.RESUMED
    assert new.status is ConversationStatus.BOT


@pytest.mark.parametrize("text", ["คุยกับ AI หน่อย หาตู้เย็น", "อยากคุยกับ AI", "คุยกับ A"])
async def test_human_resume_phrase_not_whole_message_silent(deps, text):
    conv = await _human_conv(deps)
    result, new = await _check(deps, conv, text=text)
    assert result.outcome is HandoffOutcome.SILENT
    assert new.status is ConversationStatus.HUMAN
    assert _ended(deps) == []


async def test_bot_postback_resume_ai_already_bot(deps):
    conv = await _bot_conv(deps)
    result, new = await _check(deps, conv, postback="action=resume_ai")
    assert result.outcome is HandoffOutcome.ALREADY_BOT
    assert new == conv
    assert _ended(deps) == []


async def test_bot_typed_resume_already_bot(deps):
    conv = await _bot_conv(deps)
    result, _ = await _check(deps, conv, text="คุยกับ AI")
    assert result.outcome is HandoffOutcome.ALREADY_BOT
    assert _ended(deps) == []


# --- ข้อ 3: ขอพนักงาน ---


async def test_bot_talk_to_staff_escalate_without_starting_handoff(deps):
    conv = await _bot_conv(deps)
    result, new = await _check(deps, conv, postback="action=talk_to_staff")
    assert result.outcome is HandoffOutcome.ESCALATE
    # handoff_check ไม่เรียก start_handoff เอง (orchestrator ทำ)
    assert new.status is ConversationStatus.BOT
    assert deps.notifier.started == []  # type: ignore[attr-defined]


async def test_human_talk_to_staff_silent(deps):
    conv = await _human_conv(deps)
    result, new = await _check(deps, conv, postback="action=talk_to_staff")
    assert result.outcome is HandoffOutcome.SILENT
    assert new.status is ConversationStatus.HUMAN


# --- ข้อ 4 / 5 ---


async def test_human_plain_text_silent(deps):
    conv = await _human_conv(deps)
    result, new = await _check(deps, conv, text="สวัสดีค่ะ")
    assert result.outcome is HandoffOutcome.SILENT
    assert new == conv


async def test_human_image_silent(deps):
    conv = await _human_conv(deps)
    result, _ = await _check(deps, conv, message_type="image")
    assert result.outcome is HandoffOutcome.SILENT
    assert deps.ai.calls == []  # type: ignore[attr-defined]


async def test_bot_plain_text_continue(deps):
    conv = await _bot_conv(deps)
    result, new = await _check(deps, conv, text="หาตู้เย็น 2 ประตู")
    assert result.outcome is HandoffOutcome.CONTINUE
    assert result.timed_out is False
    assert new == conv


# --- การแข่ง ---


async def test_race_cron_timeout_wins_over_resume(deps, clock):
    conv = await _human_conv(deps)  # worker ถือ conv เก่า (human)
    clock.advance(hours=6)
    # cron handoff_timeout ชนะก่อน ด้วย conv รอบเดียวกัน -> ข้อ 1 ของ handoff_check ได้ None แล้วอ่านใหม่
    assert await handoff.end_handoff(deps, conv, HandoffEndReason.TIMEOUT) is not None
    result, new = await _check(deps, conv, postback="action=resume_ai")
    assert result.outcome is HandoffOutcome.ALREADY_BOT
    assert result.timed_out is False
    assert new.status is ConversationStatus.BOT
    assert [r for _, r in _ended(deps)] == [HandoffEndReason.TIMEOUT]  # แจ้งครั้งเดียว


async def test_race_staff_close_wins_over_typed_resume(deps):
    conv = await _human_conv(deps)
    token = await deps.conversations.create_close_token(
        conv.id, conv.handoff_started_at, timedelta(hours=1)
    )
    closed = await deps.conversations.consume_close_token(token)
    assert closed is not None
    assert await handoff.end_handoff(deps, conv, HandoffEndReason.STAFF_CLOSED) is not None
    result, new = await _check(deps, conv, text="คุยกับ AI")
    assert result.outcome is HandoffOutcome.ALREADY_BOT
    assert new.handoff_end_reason is HandoffEndReason.STAFF_CLOSED
    assert [r for _, r in _ended(deps)] == [HandoffEndReason.STAFF_CLOSED]


async def test_race_cron_wins_timeout_branch_no_timed_out_flag(deps, clock):
    conv = await _human_conv(deps)
    clock.advance(hours=6)
    assert await handoff.end_handoff(deps, conv, HandoffEndReason.TIMEOUT) is not None  # cron
    result, new = await _check(deps, conv, text="หาตู้เย็น")
    # cron จัดการข้อความ/แจ้งไปแล้ว: รอบนี้ไม่ติด timed_out (ไม่นำหน้าข้อความซ้ำ)
    assert result.outcome is HandoffOutcome.CONTINUE
    assert result.timed_out is False
    assert new.status is ConversationStatus.BOT
    assert len(_ended(deps)) == 1


async def test_race_resume_after_new_handoff_round_resumes_current_round(deps, clock):
    old = await _human_conv(deps)
    clock.advance(minutes=5)
    # รอบเก่าถูกปิด แล้วเริ่มรอบใหม่ ก่อน worker จะทำ event ที่ถือ conv รอบเก่า
    assert await handoff.end_handoff(deps, old, HandoffEndReason.STAFF_CLOSED) is not None
    clock.advance(minutes=1)
    assert await deps.conversations.start_handoff(old.id, "again") is not None
    result, new = await _check(deps, old, postback="action=resume_ai")
    assert result.outcome is HandoffOutcome.RESUMED
    assert new.status is ConversationStatus.BOT
    assert [r for _, r in _ended(deps)] == [
        HandoffEndReason.STAFF_CLOSED,
        HandoffEndReason.USER_RESUMED,
    ]


# --- ผลข้างเคียงต่อ state / handoff_started_at ---


async def test_end_handoff_clears_conversation_state(deps, clock):
    conv = await _human_conv(deps)
    await deps.state.save(
        conv.id,
        state={"category": "ตู้เย็น"},
        counters=Counters(clarify_streak=1),
        expires_at=clock.now() + timedelta(hours=2),
    )
    result, _ = await _check(deps, conv, postback="action=resume_ai")
    assert result.outcome is HandoffOutcome.RESUMED
    assert await deps.state.get(conv.id) is None


async def test_human_messages_do_not_extend_state_or_handoff_start(deps, clock):
    conv = await _human_conv(deps)
    expires = clock.now() + timedelta(hours=2)
    await deps.state.save(conv.id, state={"a": 1}, counters=Counters(), expires_at=expires)
    started = conv.handoff_started_at
    for _ in range(3):
        clock.advance(hours=1)
        await deps.conversations.touch_user_message(conv.id)  # prepare ทำทุกข้อความ
        conv = await deps.conversations.get(conv.id)
        result, conv = await _check(deps, conv, text="ยังรออยู่ค่ะ")
        assert result.outcome is HandoffOutcome.SILENT
    assert conv.handoff_started_at == started
    state = await deps.state.get(conv.id)
    assert state is not None and state.expires_at == expires
    # 6 ชม. นับจาก handoff_started_at ไม่ใช่ข้อความล่าสุด
    clock.advance(hours=3)
    result, conv = await _check(deps, conv, text="ยังรออยู่ค่ะ")
    assert result.outcome is HandoffOutcome.CONTINUE and result.timed_out is True


async def test_fast_path_already_escalated_same_event_not_escalated_again(deps):
    stale = await _bot_conv(deps)
    # fast path ส่งต่อไปแล้ว (ไม่รอล็อก)
    assert await handoff.start_handoff(deps, stale, "talk_to_staff", summary="s") is not None
    # worker ทำ event เดิม: prepare อ่าน conv ใหม่ -> human -> SILENT
    fresh = await deps.conversations.get(stale.id)
    result, _ = await _check(deps, fresh, postback="action=talk_to_staff")
    assert result.outcome is HandoffOutcome.SILENT
    # แม้ถือ conv เก่า (bot) จนได้ ESCALATE -> start_handoff คืน None ไม่แจ้งซ้ำ
    result, _ = await _check(deps, stale, postback="action=talk_to_staff")
    assert result.outcome is HandoffOutcome.ESCALATE
    assert await handoff.start_handoff(deps, stale, "talk_to_staff", summary="s") is None
    assert len(deps.notifier.started) == 1  # type: ignore[attr-defined]


# --- load_resume_phrases ---


def test_load_resume_phrases_normalizes(tmp_path):
    (tmp_path / "resume_ai.txt").write_text(
        "# คำสั่งคืน AI\n\nคุยกับ AI\n คุย กับ บอท \n", encoding="utf-8"
    )
    assert load_resume_phrases(tmp_path) == frozenset({"คุยกับai", "คุยกับบอท"})


def test_load_resume_phrases_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_resume_phrases(tmp_path)
