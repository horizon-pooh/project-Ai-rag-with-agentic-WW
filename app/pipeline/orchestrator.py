"""app/pipeline/orchestrator.py

ตัวประสานงาน: เรียก step ตามลำดับ และบันทึก ai_steps ทุกขั้น

รายละเอียด:
 - ลำดับ: prepare -> handoff_check -> rules -> call_ai -> update_state -> validate -> send
 - handoff_check ได้ RESUMED / ALREADY_BOT / ESCALATE / SILENT -> ข้ามขั้นที่ไม่ต้องใช้
 - rules ตัดสินส่งต่อ/ตอบเอง -> ไม่เรียก AI
 - AiUnavailable / AiBadResponse -> fallback
 - บันทึก ai_steps เอง (step ไม่บันทึกเอง): input redact, output, latency_ms, attempts, error_class, raw_event_id, ai_run_id
 - คุมเพดานเวลารวมต่อข้อความ (TURN_DEADLINE_S)
 - ค่าเริ่มต้นเมื่อพลาดต้องปลอดภัย: ถามกลับหรือส่งต่อ ไม่ไปต่อเงียบๆ

แนวคิดจาก: src/conversation/handleMessage.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8a: PipelineConfig, load_pipeline_config, is_fast_path_event, run_turn)
       - ผู้เรียก (worker / fast path) claim event และตั้ง process_status เอง; run_turn ไม่แตะ process_status
       - ไม่ถือ transaction ข้ามการรอ AI (ทุกการเขียนเป็นคำสั่งสั้นๆ ผ่าน repositories)
       - error อื่นนอกจาก AiError ปล่อยขึ้นไป (ผู้เรียก mark_failed)
       - ความเร็ว AI API วัดแล้ว (D-011: 2-43 วิ): กลยุทธ์ "reply 'กำลังค้นหา' แล้ว push คำตอบ" ยังไม่ทำ
         ใช้ loading animation อย่างเดียว (เกินอายุ replyToken -> send ใช้ push เอง)
       - รอคำตอบ: AI API รับรูปได้ไหม — ตอนนี้ไม่ส่งรูป (SEND_IMAGES_TO_AI = False)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.logging import bind_ids
from app.pipeline import handoff, replies
from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    AiError,
    AiFlags,
    AiKind,
    AiResult,
    AiStepRecord,
    ConversationStatus,
    Counters,
    Direction,
    EventType,
    FeedbackKind,
    HandoffOutcome,
    InboundEvent,
    JsonDict,
    OutboundMessage,
    RawEvent,
    RuleOutcome,
    SendResult,
    SendStatus,
)
from app.pipeline.steps.call_ai import AiRoute, build_ai_request, call_ai, route_ai_result
from app.pipeline.steps.fallback import fallback
from app.pipeline.steps.handoff_check import (
    handoff_check,
    is_resume_command,
    is_talk_to_staff,
    load_resume_phrases,
)
from app.pipeline.steps.prepare import TurnContext, inbound_message_type, prepare
from app.pipeline.steps.rules import RuleSet, apply_rules, is_risky, load_rule_set
from app.pipeline.steps.send import send
from app.pipeline.steps.update_state import next_state, state_expiry
from app.pipeline.steps.validate import on_validate_fail, reset_validate_streak, validate
from app.privacy.redact import redact_obj, redact_text
from app.services import flex_builder

log = structlog.get_logger(__name__)

# รอคำตอบ: AI API รับรูปได้ไหม — False = เก็บรูปใน MinIO แต่ไม่ส่ง AI และรูปเดี่ยวๆ (ไม่มีข้อความ) นับเป็นข้อความว่าง
SEND_IMAGES_TO_AI = False

# event ที่ pipeline ทำ; อื่นๆ (follow/unfollow/unsend/...) ข้าม ไม่บันทึก ไม่ตอบ
# follow: ข้อความทักทายตั้งใน LINE OA Manager อยู่แล้ว บอทไม่ส่งซ้ำ
HANDLED_EVENT_TYPES = frozenset({EventType.MESSAGE, EventType.POSTBACK})

# ช่วงนับข้อความถี่ผิดปกติ (RATE_LIMIT_PER_MIN)
RATE_WINDOW = timedelta(minutes=1)

# loading animation ของ LINE รับ 5-60 วินาที ทีละ 5
LOADING_MIN_S = 5
LOADING_MAX_S = 60

# สรุปส่งพนักงาน: จำนวนข้อความย้อนหลัง และความยาวต่อบรรทัด
SUMMARY_TURNS = 4
SUMMARY_LINE_MAX = 200

# ai_run_id จาก postback feedback (ลูกค้าแก้ได้): ตัวเลข ASCII ไม่เกิน 18 หลัก (พอดี bigint)
_MAX_RUN_ID_DIGITS = 18

# purpose ของข้อความขาออก (ลง messages.message_type และ dedupe_key)
PURPOSE_RESUMED = "resumed"
PURPOSE_ALREADY_BOT = "already_bot"
PURPOSE_HANDOFF = "handoff"
PURPOSE_FAQ = "faq"
PURPOSE_EMPTY = "empty"
PURPOSE_RATE_LIMITED = "rate_limited"
PURPOSE_DETAIL = "product_detail"
PURPOSE_DETAIL_NOT_FOUND = "detail_not_found"
PURPOSE_RESTARTED = "restarted"
PURPOSE_VALIDATE_RETRY = "validate_retry"

# เหตุผลส่งต่อ (ลง conversations.handoff_reason / ai_runs.reason)
REASON_USER_TALK_TO_STAFF = "user:talk_to_staff"
REASON_VALIDATE_REPEAT = "validate:repeat_fail"


class TurnRoute(StrEnum):
    """เส้นทางของหนึ่งรอบ (ลง ai_runs.route)"""

    SKIPPED_EVENT = "skipped_event"  # event ที่ไม่ทำ / แปลงไม่ได้ — ผู้เรียกตั้ง SKIPPED ได้
    RESUMED = "resumed"
    ALREADY_BOT = "already_bot"
    SILENT = "silent"
    USER_ESCALATE = "user_escalate"  # postback talk_to_staff
    RULE_ESCALATE = "rule_escalate"
    RULE_REPLY = "rule_reply"
    RULE_IGNORE = "rule_ignore"
    RULE_EMPTY = "rule_empty"
    RATE_LIMITED = "rate_limited"
    PRODUCT_DETAIL = "product_detail"
    RESTART = "restart"
    FEEDBACK = "feedback"
    AI_ANSWER = "ai_answer"
    AI_CLARIFY = "ai_clarify"
    AI_OFFER_STAFF = "ai_offer_staff"
    AI_ESCALATE = "ai_escalate"
    AI_DOWN = "ai_down"
    AI_DOWN_ESCALATE = "ai_down_escalate"
    VALIDATE_RETRY = "validate_retry"
    VALIDATE_ESCALATE = "validate_escalate"


_AI_ROUTE_TO_TURN = {
    AiRoute.ANSWER: TurnRoute.AI_ANSWER,
    AiRoute.CLARIFY: TurnRoute.AI_CLARIFY,
    AiRoute.OFFER_STAFF: TurnRoute.AI_OFFER_STAFF,
}


@dataclass(frozen=True)
class PipelineConfig:
    """ของที่อ่านครั้งเดียวตอนเริ่ม process (ไฟล์กฎ) — ส่งเข้า run_turn ทุกครั้ง"""

    rules: RuleSet
    resume_phrases: frozenset[str]


class TurnOutcome(BaseModel):
    """ผลของหนึ่งรอบให้ผู้เรียก (worker / fast path): route, reason, สถานะของทุกการส่ง"""

    model_config = ConfigDict(frozen=True)

    route: str
    reason: str | None = None
    sent: list[SendStatus] = Field(default_factory=list)
    ai_run_id: int | None = None


def load_pipeline_config(settings: Settings) -> PipelineConfig:
    """อ่านไฟล์กฎจาก settings.rules_dir — ไฟล์ไม่มี/ผิดรูปแบบ -> error ตั้งแต่เริ่ม (fail fast)"""
    return PipelineConfig(
        rules=load_rule_set(settings.rules_dir),
        resume_phrases=load_resume_phrases(settings.rules_dir),
    )


def is_fast_path_event(event: InboundEvent, cfg: PipelineConfig) -> bool:
    """คำสั่งเปลี่ยนสถานะที่วิ่งทางด่วนได้ (ไม่รอล็อกต่อ user) — docs/Line_ai_bot.md หัวข้อ 5 ทางด่วน

    postback talk_to_staff / resume_ai, ข้อความคำสั่ง 'คุยกับ AI' (ตรงทั้งข้อความ), หรือข้อความที่กฎขั้น 5 ส่งต่อทันที
    """
    if is_talk_to_staff(event) or is_resume_command(event, cfg.resume_phrases):
        return True
    if event.event_type is EventType.MESSAGE and event.text:
        return is_risky(event.text, cfg.rules)
    return False


async def run_turn(deps: Deps, raw: RawEvent, cfg: PipelineConfig) -> TurnOutcome:
    """ทำหนึ่ง event ตั้งแต่ prepare ถึง send แล้ว finish_run

    ผู้เรียก claim event แล้ว (processing) และเป็นคนตั้ง done/failed — ที่นี่ไม่แตะ process_status
    error อื่นนอกจาก AiError (ซึ่งไป fallback) ปล่อยขึ้นไปให้ผู้เรียก mark_failed
    """
    bind_ids(raw_event_id=raw.id)
    started_at = deps.clock.now()
    try:
        event = raw.to_inbound()
    except ValueError as e:
        # event ที่ไม่มี userId/webhookEventId/timestamp (เช่น จากกลุ่ม) — ทำต่อไม่ได้ ไม่ใช่ความผิดพลาดของระบบ
        log.info("turn.skipped_event", reason="invalid_event", detail=str(e))
        return TurnOutcome(route=TurnRoute.SKIPPED_EVENT.value, reason="invalid_event")
    if event.event_type not in HANDLED_EVENT_TYPES:
        log.info("turn.skipped_event", event_type=event.event_type.value)
        return TurnOutcome(route=TurnRoute.SKIPPED_EVENT.value, reason=event.event_type.value)

    t0 = time.perf_counter()
    ctx = await prepare(deps, event)
    bind_ids(ai_run_id=ctx.ai_run_id)
    turn = _Turn(deps, cfg, ctx, started_at)
    await turn.record(
        "prepare",
        input={
            "event_type": event.event_type.value,
            "message_type": event.message_type,
            "text_redacted": ctx.text_redacted,
            "postback_data": event.postback_data,
        },
        output={
            "user_id": ctx.user.id,
            "conversation_id": ctx.conversation.id,
            "status": ctx.conversation.status.value,
            "reopened": ctx.reopened,
            "state_expired": ctx.state_expired,
            "has_state": ctx.state is not None,
            "has_image": ctx.has_image,
            "image_keys": list(ctx.image_keys),
            "has_consent": ctx.has_consent,
            "history_len": len(ctx.history),
        },
        state_before=ctx.state,
        latency_ms=_ms(t0),
    )

    route, reason = await turn.run()
    await deps.ai_runs.finish_run(ctx.ai_run_id, route=route.value, reason=reason)
    log.info(
        "turn.finished",
        route=route.value,
        reason=reason,
        sent=[s.value for s in turn.sent],
    )
    return TurnOutcome(route=route.value, reason=reason, sent=list(turn.sent), ai_run_id=ctx.ai_run_id)


# --- ตัวช่วย ---


def _ms(t0: float) -> int:
    return max(0, int((time.perf_counter() - t0) * 1000))


def _redacted(obj: Any) -> Any:
    return None if obj is None else redact_obj(obj)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _loading_seconds(deadline_s: float) -> int:
    """วินาทีของ loading animation: ปัดขึ้นทีละ 5 ใน 5-60 ตามเวลาที่เหลือ"""
    seconds = math.ceil(max(deadline_s, 0) / 5) * 5
    return min(LOADING_MAX_S, max(LOADING_MIN_S, seconds))


def _outbound_text_redacted(outbound: OutboundMessage) -> str | None:
    """ข้อความ (redact แล้ว) ของชุดที่ส่ง เก็บใน messages ให้เป็นแชทย้อนหลังของ AI: text + altText ของ flex"""
    parts: list[str] = []
    for m in outbound.messages:
        if m.get("type") == "text" and isinstance(m.get("text"), str):
            parts.append(m["text"])
        elif m.get("type") == "flex" and isinstance(m.get("altText"), str):
            parts.append(m["altText"])
    text = "\n".join(p for p in parts if p)
    return redact_text(text) if text else None


def _with_quick_reply_on_last(outbound: OutboundMessage, quick_reply: JsonDict) -> OutboundMessage:
    """ย้าย quick reply ไปไว้ที่ข้อความสุดท้ายของชุด (LINE แสดง quick reply เฉพาะข้อความสุดท้าย)"""
    messages = [dict(m) for m in outbound.messages]
    if "quickReply" not in messages[-1]:
        messages[-1]["quickReply"] = quick_reply
    return OutboundMessage(messages=messages, purpose=outbound.purpose)


def _ai_result_summary(result: AiResult) -> JsonDict:
    return {
        "kind": result.kind.value,
        "intro": result.intro,
        "products": [p.model_dump(mode="json") for p in result.products],
        "clarify_question": result.clarify_question,
        "escalate_hint": result.escalate_hint,
        "state_out": result.state_out,
        "raw": result.raw,
    }


def _ai_escalate_reason(result: AiResult) -> str:
    if result.escalate_hint is True:
        return "ai:escalate_hint"
    if result.kind is AiKind.CANNOT_ANSWER:
        return "ai:cannot_answer_repeat"
    if result.kind is AiKind.CLARIFY:
        return "ai:clarify_repeat"
    return "ai:escalate"  # pragma: no cover - route_ai_result ไม่คืน ESCALATE กรณีอื่น


def _counters_summary(counters: Counters) -> JsonDict:
    return {
        "clarify_streak": counters.clarify_streak,
        "cannot_answer_streak": counters.cannot_answer_streak,
        "validate_fail_streak": counters.validate_fail_streak,
        "last_products": [p.sku for p in counters.last_products],
    }


def _parse_run_id(value: str | None) -> int | None:
    if value and value.isascii() and value.isdigit() and len(value) <= _MAX_RUN_ID_DIGITS:
        return int(value)
    return None


class _Turn:
    """สถานะของหนึ่งรอบ (อยู่ในหน่วยความจำเฉพาะระหว่างรอบนี้) + การบันทึก ai_steps / การส่ง"""

    def __init__(
        self, deps: Deps, cfg: PipelineConfig, ctx: TurnContext, started_at: datetime
    ) -> None:
        self.deps = deps
        self.cfg = cfg
        self.ctx = ctx
        self.event = ctx.event
        self.conv = ctx.conversation
        self.started_at = started_at
        self.state: JsonDict | None = ctx.state
        self.counters: Counters = ctx.counters
        self.state_expired = ctx.state_expired
        self.timed_out = False
        self.sent: list[SendStatus] = []
        self._seq = 0

    # --- บันทึก ---

    async def record(
        self,
        step: str,
        *,
        input: JsonDict | None = None,  # ชื่อเดียวกับคอลัมน์ ai_steps.input
        output: JsonDict | None = None,
        state_before: JsonDict | None = None,
        state_after: JsonDict | None = None,
        latency_ms: int | None = None,
        attempts: int = 1,
        error_class: str | None = None,
    ) -> None:
        """เพิ่มหนึ่งแถว ai_steps (step ไม่บันทึกเอง) — ทุก string ผ่าน redact_obj ก่อนเก็บ"""
        self._seq += 1
        await self.deps.ai_runs.add_step(
            AiStepRecord(
                ai_run_id=self.ctx.ai_run_id,
                raw_event_id=self.event.raw_event_id,
                seq=self._seq,
                step=step,
                input=_redacted(input),
                output=_redacted(output),
                state_before=_redacted(state_before),
                state_after=_redacted(state_after),
                latency_ms=latency_ms,
                attempts=attempts,
                error_class=error_class,
            )
        )

    # --- ส่ง ---

    async def send(self, outbound: OutboundMessage, *, allow_when_human: bool = False) -> SendResult:
        """ส่งผ่าน sendGuard เท่านั้น; dedupe_key คงที่ต่อ (event, ลำดับการส่งในรอบนี้) กันส่งซ้ำตอน retry

        ใช้ลำดับ ไม่ใช้ purpose: retry เรียก AI ใหม่แล้วอาจได้เส้นทางอื่น (purpose ต่าง) ถ้า key ผูกกับ purpose
        ลูกค้าจะได้คำตอบสองชุด; ผูกกับลำดับ -> ข้อความแรกของรอบที่ส่งไปแล้วกลายเป็น DUPLICATE
        """
        dedupe_key = f"out:{self.event.raw_event_id}:{len(self.sent) + 1}"
        text_redacted = _outbound_text_redacted(outbound)
        t0 = time.perf_counter()
        result = await send(
            self.deps,
            conversation_id=self.conv.id,
            line_user_id=self.event.line_user_id,
            raw_event_id=self.event.raw_event_id,
            outbound=outbound,
            dedupe_key=dedupe_key,
            allow_when_human=allow_when_human,
            text_redacted=text_redacted,
        )
        self.sent.append(result.status)
        await self.record(
            "send",
            input={
                "purpose": outbound.purpose,
                "dedupe_key": dedupe_key,
                "message_types": [m.get("type") for m in outbound.messages],
                "text_redacted": text_redacted,
                "allow_when_human": allow_when_human,
            },
            output={
                "status": result.status.value,
                "via": result.via,
                "message_id": result.message_id,
            },
            latency_ms=_ms(t0),
        )
        return result

    def with_prefix(self, outbound: OutboundMessage) -> OutboundMessage:
        """เพิ่ม HANDOFF_TIMED_OUT_PREFIX เป็นข้อความแรก ถ้าเพิ่งหมดเวลาคุยกับพนักงานในรอบนี้

        ใช้กับข้อความของบอทที่ไม่ผ่าน validate (ข้อความที่ผ่าน validate ส่ง prefix เข้า validate แทน)
        """
        if not self.timed_out:
            return outbound
        return OutboundMessage(
            messages=[replies.text_message(replies.HANDOFF_TIMED_OUT_PREFIX), *outbound.messages],
            purpose=outbound.purpose,
        )

    async def reply_texts(self, purpose: str, *texts: str) -> SendResult:
        return await self.send(self.with_prefix(replies.outbound_text(purpose, *texts)))

    # --- ส่งต่อพนักงาน ---

    def handoff_summary(self) -> str:
        """สรุปสั้นๆ (redact แล้ว) ของข้อความล่าสุดให้พนักงาน"""
        lines: list[str] = []
        for turn in self.ctx.history[-SUMMARY_TURNS:]:
            who = "ลูกค้า" if turn.direction is Direction.IN else "บอท"
            lines.append(f"{who}: {_clip(turn.text_redacted, SUMMARY_LINE_MAX)}")
        if self.ctx.text_redacted:
            current = self.ctx.text_redacted
        elif self.event.event_type is EventType.POSTBACK:
            current = f"[กดปุ่ม {self.event.postback_action or '-'}]"
        elif self.ctx.has_image:
            current = "[รูปภาพ]"
        else:
            current = f"[{inbound_message_type(self.event)}]"
        lines.append(f"ลูกค้า: {_clip(current, SUMMARY_LINE_MAX)}")
        return redact_text("\n".join(lines))

    async def escalate(self, reason: str) -> bool:
        """start_handoff แล้วส่งข้อความแจ้งลูกค้า; คืน False ถ้ามีทางอื่นส่งต่อไปก่อน (ไม่ส่งอะไร)

        ข้อความส่งต่อไม่ใส่ HANDOFF_TIMED_OUT_PREFIX ("กลับมาคุยกับ AI" ขัดกับ "ส่งต่อเจ้าหน้าที่")
        """
        t0 = time.perf_counter()
        summary = self.handoff_summary()
        new = await handoff.start_handoff(self.deps, self.conv, reason, summary=summary)
        await self.record(
            "handoff",
            input={"reason": reason, "summary": summary},
            output={"started": new is not None},
            latency_ms=_ms(t0),
        )
        if new is None:
            # เป็น human อยู่แล้ว (เช่น fast path ชนะ) -> ทางนั้นแจ้งลูกค้า/พนักงานไปแล้ว ไม่ส่งซ้ำ
            log.info("turn.escalate_skipped", reason=reason)
            return False
        self.conv = new
        settings = self.deps.settings
        text = replies.handoff_text(
            in_hours=handoff.in_business_hours(self.deps.clock.now(), settings),
            hours=settings.callcenter_hours,
        )
        # status เพิ่งเป็น human ในรอบนี้เอง -> ข้อความแจ้งของระบบต้องผ่าน sendGuard ได้
        await self.send(replies.outbound_text(PURPOSE_HANDOFF, text), allow_when_human=True)
        return True

    # --- state ---

    async def save_state(self, state: JsonDict | None, counters: Counters) -> None:
        """บันทึก state + counters (ต่ออายุ STATE_TTL) เฉพาะเมื่อยังเป็น bot (ระหว่าง human ห้าม save)"""
        fresh = await self.deps.conversations.get(self.conv.id)
        saved = fresh is not None and fresh.status is ConversationStatus.BOT
        if saved:
            await self.deps.state.save(
                self.conv.id,
                state=state,
                counters=counters,
                expires_at=state_expiry(self.deps.clock.now(), self.deps.settings),
            )
        else:
            # escalationRace: ส่งต่อไประหว่างรอ AI -> ไม่เขียน state ทับ (end_handoff จะล้างอยู่แล้ว)
            log.info("turn.state_save_skipped", reason="conversation_not_bot")
        await self.record(
            "save_state",
            output={"saved": saved, "counters": _counters_summary(counters)},
            state_before=self.state,
            state_after=state if saved else None,
        )

    # --- ลำดับขั้น ---

    async def run(self) -> tuple[TurnRoute, str | None]:
        t0 = time.perf_counter()
        before = self.conv
        result, conv = await handoff_check(
            self.deps, self.event, self.conv, resume_phrases=self.cfg.resume_phrases
        )
        self.conv = conv
        self.timed_out = result.timed_out
        if result.timed_out:
            # end_handoff(timeout) ในรอบนี้ล้าง conversation_state แล้ว -> ทิ้งค่าที่ prepare อ่านไว้
            # และไม่บอก "ข้อมูลหมดอายุ" ซ้อน (HANDOFF_TIMED_OUT_PREFIX บอกแล้วว่าเริ่มคุยกับ AI ใหม่)
            self.state = None
            self.counters = Counters()
            self.state_expired = False
        await self.record(
            "handoff_check",
            input={
                "status": before.status.value,
                "event_type": self.event.event_type.value,
                "postback_action": self.event.postback_action,
            },
            output={
                "outcome": result.outcome.value,
                "timed_out": result.timed_out,
                "status": conv.status.value,
            },
            latency_ms=_ms(t0),
        )

        if result.outcome is HandoffOutcome.RESUMED:
            await self.reply_texts(PURPOSE_RESUMED, replies.RESUMED)
            return TurnRoute.RESUMED, None
        if result.outcome is HandoffOutcome.ALREADY_BOT:
            await self.reply_texts(PURPOSE_ALREADY_BOT, replies.ALREADY_BOT)
            return TurnRoute.ALREADY_BOT, None
        if result.outcome is HandoffOutcome.ESCALATE:
            await self.escalate(REASON_USER_TALK_TO_STAFF)
            return TurnRoute.USER_ESCALATE, REASON_USER_TALK_TO_STAFF
        if result.outcome is HandoffOutcome.SILENT:
            # ข้อความ/รูปบันทึกแล้วใน prepare — ระหว่าง human ไม่ตอบ ไม่ส่ง AI
            return TurnRoute.SILENT, None
        return await self.run_rules()

    async def run_rules(self) -> tuple[TurnRoute, str | None]:
        t0 = time.perf_counter()
        deps = self.deps
        count = await deps.messages.count_inbound_since(
            self.conv.id, deps.clock.now() - RATE_WINDOW
        )
        has_image = self.ctx.has_image and SEND_IMAGES_TO_AI
        rule = apply_rules(
            self.event,
            self.cfg.rules,
            recent_inbound_count=count,
            settings=deps.settings,
            has_image=has_image,
        )
        await self.record(
            "rules",
            input={
                "text_redacted": self.ctx.text_redacted,
                "postback_action": self.event.postback_action,
                "recent_inbound_count": count,
                "has_image": has_image,
            },
            output={"outcome": rule.outcome.value, "reason": rule.reason, "sku": rule.sku},
            latency_ms=_ms(t0),
        )

        match rule.outcome:
            case RuleOutcome.ESCALATE:
                reason = rule.reason or "rule:escalate"
                await self.escalate(reason)
                return TurnRoute.RULE_ESCALATE, reason
            case RuleOutcome.REPLY:
                await self.reply_texts(PURPOSE_FAQ, rule.reply_text or replies.EMPTY)
                return TurnRoute.RULE_REPLY, rule.reason
            case RuleOutcome.IGNORE:
                return TurnRoute.RULE_IGNORE, rule.reason
            case RuleOutcome.EMPTY:
                await self.reply_texts(PURPOSE_EMPTY, rule.reply_text or replies.EMPTY)
                return TurnRoute.RULE_EMPTY, rule.reason
            case RuleOutcome.RATE_LIMITED:
                # ตอบครั้งเดียวตอนเพิ่งเกิน (ข้อความที่ limit+1) ที่เหลือเงียบ ไม่งั้นบอทรัวตามลูกค้า
                if count == deps.settings.rate_limit_per_min + 1:
                    await self.reply_texts(PURPOSE_RATE_LIMITED, replies.RATE_LIMITED)
                return TurnRoute.RATE_LIMITED, rule.reason
            case RuleOutcome.PRODUCT_DETAIL:
                return await self.product_detail(rule.sku)
            case RuleOutcome.RESTART:
                return await self.restart()
            case RuleOutcome.FEEDBACK:
                return await self.feedback()
            case RuleOutcome.CONTINUE:
                return await self.run_ai()
        raise ValueError(f"ไม่รู้จัก RuleOutcome {rule.outcome}")  # pragma: no cover

    async def product_detail(self, sku: str | None) -> tuple[TurnRoute, str | None]:
        """ปุ่ม 'ดูรายละเอียด': ข้อมูลจาก counters.last_products เท่านั้น (ไม่ถาม AI)"""
        product = next((p for p in self.counters.last_products if p.sku == sku), None)
        await self.record("product_detail", input={"sku": sku}, output={"found": product is not None})
        if product is None:
            await self.reply_texts(PURPOSE_DETAIL_NOT_FOUND, replies.DETAIL_NOT_FOUND)
            return TurnRoute.PRODUCT_DETAIL, "not_found"
        outbound = OutboundMessage(
            messages=[flex_builder.product_detail_message(product)], purpose=PURPOSE_DETAIL
        )
        await self.send(self.with_prefix(outbound))
        return TurnRoute.PRODUCT_DETAIL, None

    async def restart(self) -> tuple[TurnRoute, str | None]:
        await self.deps.state.clear(self.conv.id)
        await self.record("restart", output={"cleared": True}, state_before=self.state)
        self.state = None
        self.counters = Counters()
        await self.reply_texts(PURPOSE_RESTARTED, replies.RESTARTED)
        return TurnRoute.RESTART, None

    async def feedback(self) -> tuple[TurnRoute, str | None]:
        """postback feedback: บันทึกอย่างเดียว ไม่ตอบ; ai_run_id จาก postback (แก้ได้) repo ตรวจเจ้าของเอง"""
        params = self.event.postback_params
        value = params.get("v")
        kind = {"up": FeedbackKind.THUMB_UP, "down": FeedbackKind.THUMB_DOWN}.get(value or "")
        if kind is None:
            await self.record("feedback", input={"v": value}, output={"saved": False})
            return TurnRoute.FEEDBACK, "invalid_feedback"
        run_id = _parse_run_id(params.get("run"))
        feedback_id = await self.deps.feedback.add(
            conversation_id=self.conv.id, kind=kind, value=value, ai_run_id=run_id
        )
        await self.record(
            "feedback",
            input={"v": value, "run": run_id},
            output={"saved": True, "feedback_id": feedback_id, "kind": kind.value},
        )
        return TurnRoute.FEEDBACK, kind.value

    # --- AI ---

    def remaining_s(self) -> float:
        """เวลาที่เหลือของข้อความนี้: TURN_DEADLINE_S หักเวลาที่ใช้ไปแล้วในรอบนี้ (ตาม deps.clock)"""
        elapsed = (self.deps.clock.now() - self.started_at).total_seconds()
        return self.deps.settings.turn_deadline_s - elapsed

    async def start_loading(self, deadline_s: float) -> None:
        """loading animation ระหว่างรอ AI (พังไม่เป็นไร)

        ความเร็วที่วัดจริง (D-011): ค้นสินค้า 22-43 วิ ส่วนใหญ่ยัง reply ทัน; ช้ากว่า REPLY_TOKEN_TTL_S send ใช้ push เอง
        loading animation สูงสุด 60 วิ (TURN_DEADLINE_S 90 ช่วงท้ายจึงไม่มี animation)
        ยังไม่ทำ: reply SEARCHING ทันทีแล้ว push คำตอบ — ตัดสินเมื่อเห็นสัดส่วนที่ต้อง push ในการใช้งานจริง
        """
        try:
            await self.deps.line.start_loading(
                self.event.line_user_id, seconds=_loading_seconds(deadline_s)
            )
        except Exception as e:
            log.warning("turn.start_loading_failed", error_class=type(e).__name__)

    async def run_ai(self) -> tuple[TurnRoute, str | None]:
        deadline_s = self.remaining_s()
        await self.start_loading(deadline_s)
        deadline_s = self.remaining_s()
        flags = AiFlags(
            state_expired=self.state_expired,
            just_resumed=False,
            handoff_timed_out=self.timed_out,
        )
        req = build_ai_request(
            state=self.state,
            text_redacted=self.ctx.text_redacted or "",
            history=self.ctx.history,
            flags=flags,
            images=self.ctx.image_keys if SEND_IMAGES_TO_AI else (),
        )
        req_input = {**req.model_dump(mode="json"), "deadline_s": round(deadline_s, 3)}
        t0 = time.perf_counter()
        try:
            result = await call_ai(self.deps, req, deadline_s=deadline_s)
        except AiError as e:
            await self.record(
                "call_ai",
                input=req_input,
                output={"error": type(e).__name__},
                state_before=self.state,
                latency_ms=e.latency_ms if e.latency_ms is not None else _ms(t0),
                attempts=e.attempts,
                error_class=e.error_class,
            )
            return await self.ai_fallback(e)
        await self.record(
            "call_ai",
            input=req_input,
            output=_ai_result_summary(result),
            state_before=self.state,
            latency_ms=result.latency_ms,
            attempts=result.attempts,
        )
        return await self.after_ai(result)

    async def ai_fallback(self, error: AiError) -> tuple[TurnRoute, str | None]:
        risky = is_risky(self.event.text, self.cfg.rules)
        decision = fallback(error, risky=risky)
        await self.record(
            "fallback",
            input={"risky": risky},
            output={
                "escalate": decision.escalate,
                "purpose": decision.outbound.purpose if decision.outbound else None,
            },
            error_class=decision.error_class,
        )
        if decision.escalate:
            await self.escalate(f"ai_down:{decision.error_class}")
            return TurnRoute.AI_DOWN_ESCALATE, decision.error_class
        if decision.outbound is None:  # pragma: no cover - fallback คืน outbound เสมอเมื่อไม่ส่งต่อ
            raise ValueError("fallback ไม่ส่งต่อแต่ไม่มี outbound")
        await self.send(self.with_prefix(decision.outbound))
        return TurnRoute.AI_DOWN, decision.error_class

    def prefix_texts(self, route: AiRoute) -> list[str]:
        texts: list[str] = []
        if self.timed_out:
            texts.append(replies.HANDOFF_TIMED_OUT_PREFIX)
        if self.state_expired and route is AiRoute.CLARIFY:
            texts.append(replies.STATE_EXPIRED)
        return texts

    async def after_ai(self, result: AiResult) -> tuple[TurnRoute, str | None]:
        settings = self.deps.settings
        route, counters = route_ai_result(result, self.counters, settings)
        await self.record(
            "route_ai_result",
            input={
                "kind": result.kind.value,
                "escalate_hint": result.escalate_hint,
                "counters": _counters_summary(self.counters),
            },
            output={"route": route.value, "counters": _counters_summary(counters)},
        )
        if route is AiRoute.ESCALATE:
            reason = _ai_escalate_reason(result)
            await self.escalate(reason)
            return TurnRoute.AI_ESCALATE, reason

        new_state, new_counters = self.state, counters
        if route in (AiRoute.ANSWER, AiRoute.CLARIFY):
            new_state, new_counters = next_state(self.state, result, counters)
            await self.record(
                "update_state",
                input={"has_state_out": result.state_out is not None},
                output={"counters": _counters_summary(new_counters)},
                state_before=self.state,
                state_after=new_state,
            )

        t0 = time.perf_counter()
        checked = validate(result, route, prefix_texts=self.prefix_texts(route))
        await self.record(
            "validate",
            input={"route": route.value, "prefix_texts": self.prefix_texts(route)},
            output={
                "ok": checked.ok,
                "errors": list(checked.errors),
                "purpose": checked.outbound.purpose if checked.outbound else None,
                "message_count": len(checked.outbound.messages) if checked.outbound else 0,
            },
            latency_ms=_ms(t0),
        )

        if not checked.ok:
            # คำตอบไม่ผ่าน: ไม่ใช้ state_out / products ของรอบนี้ (ไม่น่าเชื่อ) นับ streak จาก counters ของ route
            escalate_now, fail_counters = on_validate_fail(counters)
            if escalate_now:
                await self.escalate(REASON_VALIDATE_REPEAT)
                return TurnRoute.VALIDATE_ESCALATE, REASON_VALIDATE_REPEAT
            await self.reply_texts(PURPOSE_VALIDATE_RETRY, replies.VALIDATE_RETRY)
            await self.save_state(self.state, fail_counters)
            return TurnRoute.VALIDATE_RETRY, "validate_fail"

        outbound = checked.outbound
        if outbound is None:  # pragma: no cover - ValidationResult บังคับ ok=True ต้องมี outbound
            raise ValueError("validate ok แต่ไม่มี outbound")
        if route is AiRoute.CLARIFY and self.state_expired:
            # STATE_EXPIRED มาพร้อมปุ่ม "เริ่มใหม่" (replies.state_expired_message) — ย้ายไปข้อความสุดท้าย
            # เพราะ LINE แสดง quick reply เฉพาะข้อความสุดท้ายของชุด
            outbound = _with_quick_reply_on_last(
                outbound, replies.state_expired_message()["quickReply"]
            )
        await self.send(outbound)
        await self.save_state(new_state, reset_validate_streak(new_counters))
        return _AI_ROUTE_TO_TURN[route], result.kind.value


__all__ = [
    "HANDLED_EVENT_TYPES",
    "SEND_IMAGES_TO_AI",
    "PipelineConfig",
    "TurnOutcome",
    "TurnRoute",
    "is_fast_path_event",
    "load_pipeline_config",
    "run_turn",
]
