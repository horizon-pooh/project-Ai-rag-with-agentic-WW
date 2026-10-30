"""app/pipeline/steps/handoff_check.py

ขั้น 4: ดูคำสั่งคืน AI / ขอพนักงาน แล้วดูสถานะ คืน HandoffResult

รายละเอียด:
 - ลำดับ (สำคัญ):
   1. human และ now - handoff_started_at >= 6 ชม. -> end_handoff(timeout) แล้วเช็คต่อ (ตาข่ายของ cron)
   2. คำสั่ง 'คุยกับ AI' (postback action=resume_ai หรือพิมพ์ตรงทั้งข้อความหลัง normalize ตาม prompts/rules/resume_ai.txt): human -> end_handoff(user_resumed) -> RESUMED; bot -> ALREADY_BOT
   3. postback action=talk_to_staff: bot -> ESCALATE; human -> SILENT
   4. human -> SILENT (บันทึกแต่ไม่ตอบ)
   5. อื่นๆ -> CONTINUE (+ timed_out ถ้าเพิ่งหมดเวลา; เสนอ: นำหน้าคำตอบด้วย 'หมดเวลาคุยกับพนักงานแล้ว')
 - ข้อ 2 ต้องก่อนข้อ 4 ไม่งั้นคำสั่งถูกกลืน
 - normalize: NFC, ลบ zero-width, ลบช่องว่างทั้งหมด, casefold
 - 'คุยกับ AI หน่อย หาตู้เย็น' ไม่นับเป็นคำสั่ง (ตั้งใจ)
 - kill switch ไม่เช็คที่นี่ (เช็คที่ send)
 - ถ้า fast_path ทำไปแล้ว ต้องไม่ทำซ้ำ
 - ESCALATE = "orchestrator ต้องเรียก handoff.start_handoff" ที่นี่ไม่เรียกเอง และไม่ส่งข้อความเอง
 - end_handoff ได้ None (แข่งกับ cron / /close / fast path) -> อ่าน conversation ใหม่แล้วตัดสินจากสถานะล่าสุด
   (ไม่แจ้งพนักงานซ้ำ ไม่ RESUMED ซ้ำ)
 - timed_out=True ติดไปกับผลทุกแบบเมื่อข้อ 1 สำเร็จในรอบนี้ (orchestrator ใช้กับ CONTINUE ตามแบบหลัก)

แนวคิดจาก: src/conversation/handleMessage.js, src/conversation/stateMachine.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 4)
"""

from __future__ import annotations

from pathlib import Path

import structlog

from app.pipeline import handoff
from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    Conversation,
    ConversationStatus,
    EventType,
    HandoffEndReason,
    HandoffOutcome,
    HandoffResult,
    InboundEvent,
    PostbackAction,
)
from app.pipeline.text import load_rule_lines, normalize

log = structlog.get_logger(__name__)

RESUME_AI_FILE = "resume_ai.txt"


def load_resume_phrases(rules_dir: str | Path) -> frozenset[str]:
    """คำสั่ง 'คุยกับ AI' จาก <rules_dir>/resume_ai.txt (normalize แล้ว) — ไฟล์ไม่มี -> FileNotFoundError (รู้ตั้งแต่เริ่ม)"""
    return frozenset(load_rule_lines(Path(rules_dir) / RESUME_AI_FILE))


def _is_human(conv: Conversation) -> bool:
    return conv.status is ConversationStatus.HUMAN


def is_resume_command(event: InboundEvent, resume_phrases: frozenset[str]) -> bool:
    """postback action=resume_ai หรือข้อความที่ normalize แล้วตรงทั้งข้อความกับรายการใน resume_ai.txt"""
    if event.event_type is EventType.POSTBACK:
        return event.postback_action == PostbackAction.RESUME_AI
    if event.text is None:
        return False
    return normalize(event.text) in resume_phrases


def is_talk_to_staff(event: InboundEvent) -> bool:
    return (
        event.event_type is EventType.POSTBACK
        and event.postback_action == PostbackAction.TALK_TO_STAFF
    )


async def _reread(deps: Deps, conv: Conversation) -> Conversation:
    """อ่านสถานะล่าสุดหลังแพ้การแข่ง; ไม่เจอแถว (ไม่ควรเกิด) -> ใช้ของเดิม"""
    fresh = await deps.conversations.get(conv.id)
    if fresh is None:
        log.warning("handoff_check_conversation_missing", conversation_id=conv.id)
        return conv
    return fresh


async def _timeout_if_due(deps: Deps, conv: Conversation) -> tuple[Conversation, bool]:
    """ข้อ 1: human เกิน HANDOFF_TTL นับจาก handoff_started_at -> end_handoff(timeout)

    คืน (conversation ล่าสุด, timed_out); แพ้ cron -> อ่านใหม่ timed_out=False (cron จัดการข้อความ/แจ้งแล้ว)
    """
    started = conv.handoff_started_at
    if not _is_human(conv) or started is None:
        return conv, False
    if deps.clock.now() - started < deps.settings.handoff_ttl:
        return conv, False
    new = await handoff.end_handoff(deps, conv, HandoffEndReason.TIMEOUT)
    if new is not None:
        return new, True
    return await _reread(deps, conv), False


async def _resume(
    deps: Deps, conv: Conversation, timed_out: bool
) -> tuple[HandoffResult, Conversation]:
    """ข้อ 2: คำสั่งคืน AI; แพ้การแข่ง -> ตัดสินจากสถานะล่าสุด"""
    # ลองได้ 2 รอบ: รอบที่ 2 สำหรับกรณีที่อ่านใหม่แล้วยัง human แต่เป็นรอบส่งต่อใหม่ (handoff_started_at เปลี่ยน)
    # คำสั่งของลูกค้าใช้กับรอบปัจจุบัน; เกินนั้นยัง human -> SILENT (ปลอดภัย: ไม่ตอบระหว่าง human)
    for _ in range(2):
        if not _is_human(conv):
            return HandoffResult(outcome=HandoffOutcome.ALREADY_BOT, timed_out=timed_out), conv
        new = await handoff.end_handoff(deps, conv, HandoffEndReason.USER_RESUMED)
        if new is not None:
            return HandoffResult(outcome=HandoffOutcome.RESUMED, timed_out=timed_out), new
        fresh = await _reread(deps, conv)
        if fresh.handoff_started_at == conv.handoff_started_at and _is_human(fresh):
            # รอบเดิมแต่ยัง human (ไม่ควรเกิด) -> ลองซ้ำไม่มีประโยชน์
            conv = fresh
            break
        conv = fresh
    if _is_human(conv):
        return HandoffResult(outcome=HandoffOutcome.SILENT, timed_out=timed_out), conv
    return HandoffResult(outcome=HandoffOutcome.ALREADY_BOT, timed_out=timed_out), conv


async def handoff_check(
    deps: Deps,
    event: InboundEvent,
    conv: Conversation,
    *,
    resume_phrases: frozenset[str],
) -> tuple[HandoffResult, Conversation]:
    """ตามตาราง 5 ข้อใน docs/Line_ai_bot.md หัวข้อ 5 (ลำดับสำคัญ) คืน (ผล, conversation ล่าสุด)"""
    # 1. หมดเวลา 6 ชม. (ตาข่ายของ cron) แล้วเช็คข้อถัดไปต่อด้วยสถานะใหม่
    conv, timed_out = await _timeout_if_due(deps, conv)

    # 2. คำสั่งคืน AI — ต้องก่อนข้อ 4 ไม่งั้นถูกกลืน
    if is_resume_command(event, resume_phrases):
        return await _resume(deps, conv, timed_out)

    # 3. คำสั่งขอพนักงาน: bot -> ESCALATE (orchestrator เรียก start_handoff); human -> SILENT (fast path ทำไปแล้ว ฯลฯ)
    if is_talk_to_staff(event):
        if _is_human(conv):
            return HandoffResult(outcome=HandoffOutcome.SILENT, timed_out=timed_out), conv
        return HandoffResult(outcome=HandoffOutcome.ESCALATE, timed_out=timed_out), conv

    # 4. human -> เงียบ (ข้อความ/รูปบันทึกแล้วที่ prepare ไม่ส่ง AI)
    if _is_human(conv):
        return HandoffResult(outcome=HandoffOutcome.SILENT, timed_out=timed_out), conv

    # 5. ไปขั้น rules
    return HandoffResult(outcome=HandoffOutcome.CONTINUE, timed_out=timed_out), conv
