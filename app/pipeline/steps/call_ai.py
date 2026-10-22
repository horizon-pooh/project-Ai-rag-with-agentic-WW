"""app/pipeline/steps/call_ai.py

ขั้น 6: เรียก AI API ครั้งเดียว เห็นแค่ AiRequest / AiResult

รายละเอียด:
 - สร้าง AiRequest จาก TurnContext (ข้อความ redact แล้วเท่านั้น)
 - เรียก services/ai_client ส่ง deadline ที่เหลือของข้อความนี้ไปด้วย
 - ไม่รู้จักรูปแบบ HTTP ของ API (อยู่ใน ai_client ที่เดียว)
 - ลองซ้ำ: timeout/ต่อไม่ได้/429/5xx -> ซ้ำ 1 ครั้งถ้าเวลาเหลือพอ (เคารพ retry-after); 4xx อื่น/อ่านไม่ออก -> ไม่ซ้ำ
 - รวมทุกรอบไม่เกิน deadline (เพดานเวลารวมต่อข้อความ)
 - AiUnavailable / AiBadResponse ส่งต่อให้ orchestrator -> fallback
 - ระหว่างรอ: เร็ว -> loading animation + reply; เกินอายุ replyToken -> reply 'กำลังค้นหา' แล้ว push; ช้ามาก -> บอกเวลารอ / เลิกรอแล้วส่งต่อ (รอคำตอบเรื่องความเร็ว API)
 - ไม่ทำ agent ไม่ทำ tool-calling ไม่เรียก Search API เอง

แนวคิดจาก: src/ai/aiClient.js, src/jobs/softnixQueue.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (build_ai_request, call_ai, route_ai_result) — การลองซ้ำอยู่ใน services/ai_client;
       ข้อความระหว่างรอ (loading / 'กำลังค้นหา') เป็นงานของ orchestrator (งาน 8)
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from app.config import Settings
from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    AiFlags,
    AiKind,
    AiRequest,
    AiResult,
    AiUnavailable,
    ChatTurn,
    Counters,
)

# จำนวนข้อความย้อนหลังสูงสุดที่ส่ง AI (ตรงกับ AiRequest.recent_messages max_length)
MAX_HISTORY = 4

# cannot_answer ติดกันครบเท่านี้ -> ส่งต่อ (ครั้งแรกเสนอปุ่ม) — docs/PLAN.md เกณฑ์ (เสนอ)
CANNOT_ANSWER_ESCALATE_AT = 2


class AiRoute(StrEnum):
    """เส้นทางที่โค้ดตัดสินจาก AiResult (AI คืนข้อมูล โค้ดตัดสินสุดท้าย)"""

    ESCALATE = "escalate"  # ส่งต่อพนักงาน
    OFFER_STAFF = "offer_staff"  # ตอบ CANNOT_ANSWER + ปุ่มคุยกับเจ้าหน้าที่
    CLARIFY = "clarify"  # ถามกลับหนึ่งคำถาม
    ANSWER = "answer"  # ไป update_state -> validate -> send


def build_ai_request(
    *,
    state: dict[str, Any] | None,
    text_redacted: str,
    history: Sequence[ChatTurn],
    flags: AiFlags,
    images: Sequence[str] = (),
) -> AiRequest:
    """สร้าง AiRequest — ผู้เรียกต้องส่งข้อความที่ผ่าน app/privacy/redact.py แล้วเท่านั้น

    - history เรียงเก่าไปใหม่ (ตาม MessagesRepo.recent_texts_redacted) ส่งแค่ MAX_HISTORY ข้อความท้าย
    - images: storage_key ของรูปที่ย่อแล้ว — รอคำตอบ: AI API รับรูปได้ไหม (ตอนนี้ orchestrator ส่ง () ตาม docs/PLAN.md)
    """
    recent = list(history)[-MAX_HISTORY:] if MAX_HISTORY > 0 else []
    return AiRequest(
        state=state,
        text_redacted=text_redacted,
        recent_messages=recent,
        images=list(images),
        flags=flags,
    )


async def call_ai(deps: Deps, req: AiRequest, *, deadline_s: float) -> AiResult:
    """เรียก AI API ผ่าน deps.ai ครั้งเดียว (ลองซ้ำอยู่ใน ai_client)

    deadline_s = เวลาที่เหลือของข้อความนี้ (TURN_DEADLINE_S หักเวลาที่ใช้ไปแล้ว) — หมดแล้ว -> AiUnavailable
    ทันทีโดยไม่เรียก API. AiUnavailable / AiBadResponse ปล่อยขึ้นไปให้ orchestrator เรียก fallback
    """
    if deadline_s <= 0:
        # attempts=0: ไม่ได้เรียก API เลย
        raise AiUnavailable("เวลาของข้อความนี้หมดก่อนเรียก AI", error_class="deadline", attempts=0)
    return await deps.ai.call(req, deadline_s)


def route_ai_result(
    result: AiResult, counters: Counters, settings: Settings
) -> tuple[AiRoute, Counters]:
    """เลือกเส้นทางจาก AiResult + ตัวนับสัญญาณหลังเกิดเหตุ (ฟังก์ชันล้วน ไม่แตะ I/O)

    ตาราง docs/Line_ai_bot.md ขั้น call_ai + เกณฑ์ใน docs/PLAN.md:
     - escalate_hint = True -> ESCALATE (AI ยกระดับได้เสมอ; รอคำตอบ: API บอก escalate_hint ได้ไหม)
     - cannot_answer -> cannot_answer_streak+1: ครั้งแรก OFFER_STAFF, ติดกันครั้งที่ 2 -> ESCALATE
     - clarify -> clarify_streak+1: เกิน settings.clarify_max -> ESCALATE ไม่งั้น CLARIFY
     - products / answer -> ANSWER และล้าง clarify_streak / cannot_answer_streak
    "ติดกัน" = รอบต่อกันชนิดเดียวกัน: clarify ล้าง cannot_answer_streak และกลับกัน
    ส่งต่อแล้วล้าง streak ทั้งสอง (เริ่มนับใหม่เมื่อกลับมาคุยกับ AI); validate_fail_streak / last_products ไม่แตะ
    counters เดิมไม่ถูกแก้ (คืนสำเนาใหม่)
    """
    reset = {"clarify_streak": 0, "cannot_answer_streak": 0}

    if result.escalate_hint is True:
        return AiRoute.ESCALATE, counters.model_copy(update=reset)

    if result.kind is AiKind.CANNOT_ANSWER:
        streak = counters.cannot_answer_streak + 1
        if streak >= CANNOT_ANSWER_ESCALATE_AT:
            return AiRoute.ESCALATE, counters.model_copy(update=reset)
        return AiRoute.OFFER_STAFF, counters.model_copy(
            update={"cannot_answer_streak": streak, "clarify_streak": 0}
        )

    if result.kind is AiKind.CLARIFY:
        streak = counters.clarify_streak + 1
        if streak > settings.clarify_max:
            return AiRoute.ESCALATE, counters.model_copy(update=reset)
        return AiRoute.CLARIFY, counters.model_copy(
            update={"clarify_streak": streak, "cannot_answer_streak": 0}
        )

    # products / answer
    return AiRoute.ANSWER, counters.model_copy(update=reset)
