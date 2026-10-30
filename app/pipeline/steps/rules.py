"""app/pipeline/steps/rules.py

ขั้น 5: กฎในโค้ดที่ตัดสินก่อน AI

รายละเอียด:
 - ร้องเรียน / เคลม / ขอคนชัดเจน -> ส่งต่อทันที ไม่เรียก AI (prompts/rules/escalate.txt)
 - FAQ / ทักทาย -> ตอบเองไม่ใช้ AI (prompts/rules/faq.txt)
 - keyword ที่ Vendor 1 ตอบเอง -> ไม่ตอบ
 - ปุ่ม 'ดูรายละเอียด' ใช้ SKU จาก postback ตรง ไม่ผ่าน AI
 - ข้อความว่างหรือถี่ผิดปกติ -> ตอบสั้นหรือจำกัดอัตรา
 - AI ยกระดับเป็นส่งต่อได้ แต่ห้ามลดทอนการส่งต่อที่กฎนี้ตัดสินแล้ว
 - ทุกคำในไฟล์กฎต้องมีเคสทดสอบภาษาไทยจริง
 - การจับคำ: escalate = มีอยู่ใน (substring) ข้อความหลัง normalize; faq = ตรงทั้งข้อความหลัง normalize
 - ลำดับใน apply_rules: postback detail -> restart -> feedback -> (postback อื่น IGNORE) -> escalate
   -> ข้อความว่าง -> ถี่ผิดปกติ -> FAQ ของ Vendor 1 (IGNORE) -> FAQ (REPLY) -> CONTINUE
 - ฟังก์ชันล้วน: ไม่มี I/O นอกจาก load_rule_set (อ่านไฟล์ครั้งเดียวตอนเริ่ม); จำนวนข้อความต่อนาที orchestrator นับมาให้
 - is_risky ใช้ร่วมกับ fallback (AI ล่ม + ข้อความเข้าข่ายเสี่ยง -> ส่งต่อ)

แนวคิดจาก: src/pipeline/filter.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 4)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from app.config import Settings
from app.pipeline import replies
from app.pipeline.schemas import (
    EventType,
    InboundEvent,
    PostbackAction,
    RuleOutcome,
    RuleResult,
)
from app.pipeline.text import load_faq, load_rule_lines, normalize

ESCALATE_FILE = "escalate.txt"
FAQ_FILE = "faq.txt"


@dataclass(frozen=True)
class RuleSet:
    """กฎที่อ่านจาก prompts/rules/ แล้ว (ทุกคำ normalize แล้ว)

    escalate: รายการ substring เรียงตามไฟล์ (ตัวแรกที่ตรงเป็น reason)
    faq: {pattern ตรงทั้งข้อความ: คำตอบ}; คำตอบ None = keyword ของ Vendor 1 (บอทไม่ตอบ)
    """

    escalate: tuple[str, ...] = ()
    faq: Mapping[str, str | None] = field(default_factory=lambda: MappingProxyType({}))


def load_rule_set(rules_dir: str | Path) -> RuleSet:
    """อ่าน escalate.txt และ faq.txt จาก rules_dir — ไฟล์ไม่มี/ผิดรูปแบบ -> error ตั้งแต่เริ่ม"""
    d = Path(rules_dir)
    return RuleSet(
        escalate=tuple(load_rule_lines(d / ESCALATE_FILE)),
        faq=MappingProxyType(load_faq(d / FAQ_FILE)),
    )


def match_escalate(text: str | None, rules: RuleSet) -> str | None:
    """รายการแรกใน escalate.txt ที่อยู่ในข้อความ (หลัง normalize); ไม่ตรง -> None"""
    if not text:
        return None
    n = normalize(text)
    if not n:
        return None
    return next((entry for entry in rules.escalate if entry in n), None)


def is_risky(text: str | None, rules: RuleSet) -> bool:
    """ข้อความเข้าข่ายต้องส่งต่อตาม escalate.txt ไหม (fallback ใช้ตอน AI ล่ม)"""
    return match_escalate(text, rules) is not None


def _postback_rule(event: InboundEvent) -> RuleResult:
    action = event.postback_action
    if action == PostbackAction.DETAIL:
        sku = (event.postback_params.get("sku") or "").strip()
        if sku:
            return RuleResult(outcome=RuleOutcome.PRODUCT_DETAIL, sku=sku)
        # ปุ่มของเราใส่ sku เสมอ ไม่มี = data ผิดรูป -> ไม่ตอบ (ไม่ส่ง AI)
        return RuleResult(outcome=RuleOutcome.IGNORE, reason="detail_without_sku")
    if action == PostbackAction.RESTART:
        return RuleResult(outcome=RuleOutcome.RESTART)
    if action == PostbackAction.FEEDBACK:
        return RuleResult(outcome=RuleOutcome.FEEDBACK)
    # talk_to_staff / resume_ai จัดการที่ handoff_check แล้ว ไม่ควรมาถึงนี่;
    # postback ที่ไม่รู้จัก (data ปลอม/ปุ่มเก่า) -> ไม่ตอบและไม่ส่ง AI (ค่าเริ่มต้นที่ปลอดภัย)
    return RuleResult(outcome=RuleOutcome.IGNORE, reason="unknown_postback")


def apply_rules(
    event: InboundEvent,
    rules: RuleSet,
    *,
    recent_inbound_count: int,
    settings: Settings,
    has_image: bool = False,
) -> RuleResult:
    """ตัดสินก่อน AI ตามลำดับในหัวไฟล์

    recent_inbound_count = จำนวนข้อความขาเข้าในหนึ่งนาทีล่าสุด (รวมข้อความนี้) จาก count_inbound_since
    has_image = รอบนี้มีรูป (ข้อความว่างแต่มีรูป ไม่นับเป็นว่าง)
    """
    if event.event_type is EventType.POSTBACK:
        return _postback_rule(event)

    # ส่งต่อมาก่อนทุกอย่าง (รวมก่อนจำกัดอัตรา): ลูกค้าร้องเรียนรัวๆ ต้องถึงคน
    entry = match_escalate(event.text, rules)
    if entry is not None:
        return RuleResult(outcome=RuleOutcome.ESCALATE, reason=f"rule:{entry}")

    n = normalize(event.text) if event.text else ""
    if not n and not has_image:
        return RuleResult(outcome=RuleOutcome.EMPTY, reply_text=replies.EMPTY)

    if recent_inbound_count > settings.rate_limit_per_min:
        return RuleResult(outcome=RuleOutcome.RATE_LIMITED, reason="rate_limit")

    if n in rules.faq:
        answer = rules.faq[n]
        if answer is None:
            return RuleResult(outcome=RuleOutcome.IGNORE, reason="vendor1")
        return RuleResult(outcome=RuleOutcome.REPLY, reason="faq", reply_text=answer)

    return RuleResult(outcome=RuleOutcome.CONTINUE)
