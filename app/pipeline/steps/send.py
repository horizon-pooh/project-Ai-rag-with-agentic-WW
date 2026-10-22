"""app/pipeline/steps/send.py

ขั้น 9: ส่งข้อความผ่าน sendGuard เท่านั้น (ด่านเดียวของข้อความขาออก)

รายละเอียด:
 - อ่านสถานะล่าสุดจาก DB ก่อนส่งทุกครั้ง (สถานะเปลี่ยนได้ระหว่างรอ AI)
 - status = human -> ทิ้งคำตอบ AI + log (กันคำตอบที่มาช้าไปแทรกพนักงาน)
 - kill switch เปิด -> ไม่ส่ง + log
 - (เสนอ) มี event ใหม่ค้าง pending -> ทิ้งคำตอบเก่าแล้วตอบรวมรอบหน้า หรือส่งไปก่อน (ตัดสินหลังรู้ความเร็ว API)
 - บันทึกสถานะขาออกก่อนส่ง (กันส่งซ้ำตอน retry)
 - replyToken ยังใช้ได้ -> reply; หมดอายุ -> push
 - บันทึกผล, log ทุกการตัดสิน (ส่ง / ทิ้ง + เหตุผล)
 - เรียก LINE ผ่าน services/line_client เท่านั้น

แนวคิดจาก: src/line/sendGuard.js (แบบเก่า, D-003)
สถานะ: มีโค้ดแล้ว (send) — ข้อ "(เสนอ) event ใหม่ค้าง pending" ยังไม่ทำ (ยังไม่ตัดสิน)
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import structlog

from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    ConversationStatus,
    JsonDict,
    LineApiError,
    OutboundMessage,
    OutboundStatus,
    SendResult,
    SendStatus,
)

log = structlog.get_logger(__name__)

# namespace คงที่ของ uuid5 -> X-Line-Retry-Key (ห้ามเปลี่ยน ไม่งั้น retry ข้าม deploy จะได้ key ใหม่และส่งซ้ำ)
RETRY_KEY_NAMESPACE = uuid.UUID("6f1c2b0e-4d1a-5c8e-9a57-2f0b3c4d5e6f")

# เหตุผลที่ลง messages.drop_reason
REASON_KILL_SWITCH = "kill_switch"
REASON_HUMAN = "conversation_human"


def push_retry_key(dedupe_key: str) -> str:
    """retry key ของ push: uuid5 จาก dedupe_key -> คงที่ทุกรอบ retry ให้ LINE กันส่งซ้ำ"""
    return str(uuid.uuid5(RETRY_KEY_NAMESPACE, dedupe_key))


def _is_bad_reply_token(e: LineApiError) -> bool:
    """reply พังเพราะ replyToken ใช้ไม่ได้/หมดอายุ (4xx ที่ไม่ควรลองซ้ำ) -> ลอง push แทนได้หนึ่งครั้ง"""
    return not e.retryable and e.status_code is not None and 400 <= e.status_code < 500


def _is_retry_key_accepted(e: LineApiError) -> bool:
    """push ด้วย retry key ที่ LINE รับไปแล้ว -> 409 Conflict = ข้อความชุดนี้ส่งสำเร็จไปแล้วรอบก่อน"""
    return e.status_code == 409


async def send(
    deps: Deps,
    *,
    conversation_id: int,
    line_user_id: str,
    raw_event_id: int | None,
    outbound: OutboundMessage,
    dedupe_key: str,
    allow_when_human: bool = False,
    text_redacted: str | None = None,
) -> SendResult:
    """ทางเดียวที่ข้อความขาออกไปถึง LINE (sendGuard)

    ลำดับการตัดสิน:
     1. kill switch เปิด -> DROPPED_KILL_SWITCH (ไม่เรียก LINE)
     2. อ่าน conversation ล่าสุด: status=human และไม่ allow_when_human -> DROPPED_HUMAN
        (= เคส escalationRace: ลูกค้าขอพนักงานระหว่างรอ AI แล้วคำตอบ AI มาถึงทีหลัง)
     3. reserve_outbound(dedupe_key): ส่งไปแล้ว -> DUPLICATE (มาก่อนข้อ 1-2 ในผล: แถวที่ sent แล้วไม่ถูกเขียนทับ)
     4. มี replyToken ของ raw_event_id -> reply; ไม่มี/หมดอายุ -> push (retry key = uuid5 จาก dedupe_key)
        reply ได้ 4xx ที่ไม่ควรลองซ้ำ (replyToken ใช้ไม่ได้) -> push หนึ่งครั้ง
     5. mark_outbound SENT / FAILED (+ เหตุผล)
    ทิ้งข้อความ (1, 2) -> บันทึกแถว status=dropped พร้อมเหตุผล ถ้ามี conversation (แถวที่ส่งไปแล้วไม่แตะ)

    allow_when_human: ใช้เฉพาะข้อความแจ้งของระบบเองตอนเพิ่งส่งต่อ (เช่น HANDOFF_IN_HOURS ที่ส่งหลัง
    status เพิ่งเป็น human) — คำตอบของ AI ห้ามส่ง allow_when_human=True เด็ดขาด
    text_redacted: ข้อความ (redact แล้ว) ที่เก็บใน messages ให้ใช้เป็นแชทย้อนหลังส่ง AI ได้ (ผู้เรียก redact เอง)
    log ทุกการตัดสิน — raw_event_id / ai_run_id ผู้เรียก bind ไว้แล้ว (app/logging.bind_ids)

    ยังไม่ทำ (เสนอ ยังไม่ตัดสิน รอความเร็ว API): มี event ใหม่ของ user ค้าง pending -> ทิ้งคำตอบเก่า
    แล้วตอบรวมรอบหน้า หรือส่งไปก่อน
    """
    blog = log.bind(conversation_id=conversation_id, purpose=outbound.purpose)

    kill = await deps.kill_switch.is_on()
    conv = await deps.conversations.get(conversation_id)

    if conv is None:
        # ไม่มี conversation -> บันทึกแถวไม่ได้ (FK) และตรวจสถานะไม่ได้ -> ไม่ส่ง (ค่าเริ่มต้นที่ปลอดภัย)
        if kill:
            blog.info(
                "send.dropped",
                decision=SendStatus.DROPPED_KILL_SWITCH.value,
                reason=REASON_KILL_SWITCH,
            )
            return SendResult(status=SendStatus.DROPPED_KILL_SWITCH)
        blog.error("send.no_conversation", decision=SendStatus.FAILED.value)
        return SendResult(status=SendStatus.FAILED)

    drop: tuple[SendStatus, str] | None = None
    if kill:
        drop = (SendStatus.DROPPED_KILL_SWITCH, REASON_KILL_SWITCH)
    elif conv.status is ConversationStatus.HUMAN and not allow_when_human:
        drop = (SendStatus.DROPPED_HUMAN, REASON_HUMAN)

    reservation = await deps.messages.reserve_outbound(
        conversation_id=conversation_id,
        raw_event_id=raw_event_id,
        dedupe_key=dedupe_key,
        outbound=outbound,
        text_redacted=text_redacted,
    )
    message_id = reservation.message_id

    if reservation.already_sent:
        # ส่งสำเร็จไปแล้วรอบก่อน (retry) -> ไม่ส่งซ้ำ และไม่เขียนทับแถวที่ sent แล้วเป็น dropped
        blog.info(
            "send.duplicate",
            decision=SendStatus.DUPLICATE.value,
            message_id=message_id,
            would_drop=drop[1] if drop else None,
        )
        return SendResult(status=SendStatus.DUPLICATE, message_id=message_id)

    if drop is not None:
        status, reason = drop
        await deps.messages.mark_outbound(message_id, OutboundStatus.DROPPED, reason=reason)
        blog.info("send.dropped", decision=status.value, reason=reason, message_id=message_id)
        return SendResult(status=status, message_id=message_id)

    messages = list(outbound.messages)
    via: Literal["reply", "push"]
    try:
        token = await deps.reply_tokens.take(raw_event_id) if raw_event_id is not None else None
        if token is not None:
            try:
                await deps.line.reply(token, messages)
                via = "reply"
            except LineApiError as e:
                if not _is_bad_reply_token(e):
                    raise
                blog.warning(
                    "send.reply_token_rejected", status_code=e.status_code, message_id=message_id
                )
                via = await _push(deps, line_user_id, messages, dedupe_key, blog, message_id)
        else:
            via = await _push(deps, line_user_id, messages, dedupe_key, blog, message_id)
    except LineApiError as e:
        reason = f"line_error:{e.status_code if e.status_code is not None else 'network'}"
        await deps.messages.mark_outbound(message_id, OutboundStatus.FAILED, reason=reason)
        blog.error(
            "send.failed",
            decision=SendStatus.FAILED.value,
            reason=reason,
            retryable=e.retryable,
            message_id=message_id,
        )
        return SendResult(status=SendStatus.FAILED, message_id=message_id)

    await deps.messages.mark_outbound(message_id, OutboundStatus.SENT)
    blog.info("send.sent", decision=SendStatus.SENT.value, via=via, message_id=message_id)
    return SendResult(status=SendStatus.SENT, via=via, message_id=message_id)


async def _push(
    deps: Deps,
    line_user_id: str,
    messages: list[JsonDict],
    dedupe_key: str,
    blog: Any,
    message_id: int,
) -> Literal["push"]:
    """push ด้วย retry key คงที่; 409 (LINE รับ retry key นี้ไปแล้ว) ถือว่าส่งสำเร็จ"""
    try:
        await deps.line.push(line_user_id, messages, retry_key=push_retry_key(dedupe_key))
    except LineApiError as e:
        if not _is_retry_key_accepted(e):
            raise
        blog.info("send.push_already_accepted", status_code=e.status_code, message_id=message_id)
    return "push"
