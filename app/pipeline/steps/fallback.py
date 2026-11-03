"""app/pipeline/steps/fallback.py

คำตอบสำรองเมื่อ AI ล่มหรืออ่านผลไม่ออก

รายละเอียด:
 - เรียกโดย orchestrator เมื่อได้ AiUnavailable / AiBadResponse
 - ข้อความเข้าข่ายเสี่ยงตามกฎขั้น 5 -> ส่งต่อพนักงาน
 - ไม่เข้าข่าย -> ขอโทษ + ปุ่มคุยกับพนักงาน
 - บันทึก error เสมอ (error_class ใน ai_steps)
 - ส่งผ่าน send (sendGuard) เหมือนข้อความอื่น

แนวคิดจาก: src/conversation/escalate.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (fallback เป็นฟังก์ชันล้วน) — orchestrator (งาน 8) บันทึก ai_steps และส่งผ่าน sendGuard
"""

from __future__ import annotations

from dataclasses import dataclass

from app.pipeline import replies
from app.pipeline.schemas import AiError, OutboundMessage

PURPOSE_AI_DOWN = "ai_down"


@dataclass(frozen=True, kw_only=True)
class FallbackDecision:
    """ผลของ fallback

    escalate=True -> orchestrator ส่งต่อพนักงานและส่งข้อความ handoff เอง (outbound=None)
    escalate=False -> ส่ง outbound (AI_DOWN + ปุ่มคุยกับเจ้าหน้าที่)
    error_class -> ลง ai_steps.error_class เสมอ
    """

    escalate: bool
    outbound: OutboundMessage | None
    error_class: str


def fallback(error: AiError, *, risky: bool) -> FallbackDecision:
    """ตัดสินคำตอบสำรอง: risky (ข้อความเข้าข่ายเสี่ยงตามกฎขั้น 5) -> ส่งต่อ, ไม่งั้นขอโทษ + ปุ่ม"""
    if risky:
        return FallbackDecision(escalate=True, outbound=None, error_class=error.error_class)
    return FallbackDecision(
        escalate=False,
        outbound=OutboundMessage(messages=[replies.ai_down_message()], purpose=PURPOSE_AI_DOWN),
        error_class=error.error_class,
    )
