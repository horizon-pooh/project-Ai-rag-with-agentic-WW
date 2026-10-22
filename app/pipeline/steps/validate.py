"""app/pipeline/steps/validate.py

ขั้น 8: ตรวจคำตอบก่อนส่ง

รายละเอียด:
 - SKU / ราคา / ลิงก์ ต้องตรงกับ products ที่ API คืนมา (AI ห้ามแต่งเอง)
 - ไม่เกินขีดจำกัดของ LINE (จำนวนการ์ด, ความยาวข้อความ, จำนวน quick reply)
 - ไม่ผ่าน -> ถามกลับหรือส่งต่อ; ไม่ผ่านซ้ำ -> ส่งต่อ
 - kind = cannot_answer หรือ escalate_hint -> ส่งต่อ (ถ้า API บอกได้ รอคำตอบข้อ 4)

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว (validate, on_validate_fail, reset_validate_streak เป็นฟังก์ชันล้วน)
       เส้นทาง escalate ตัดสินที่ call_ai.route_ai_result; orchestrator (งาน 8) ส่ง VALIDATE_RETRY / ส่งต่อตามผล on_validate_fail
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from app.pipeline import replies
from app.pipeline.schemas import (
    LINE_MAX_MESSAGES,
    LINE_MAX_TEXT_CHARS,
    MAX_CAROUSEL_BUBBLES,
    AiResult,
    Counters,
    JsonDict,
    OutboundMessage,
    Product,
    ValidationResult,
)
from app.pipeline.steps.call_ai import AiRoute
from app.services import flex_builder

# validate ไม่ผ่านติดกันครบเท่านี้ -> ส่งต่อ (ครั้งแรกถามกลับ VALIDATE_RETRY) — docs/PLAN.md เกณฑ์ (เสนอ)
VALIDATE_FAIL_ESCALATE_AT = 2

# ขีดจำกัดของ LINE (เอกสาร Messaging API): uri action ≤1000, image url ≤2000, postback data ≤300
LINE_MAX_URI_CHARS = 1000
LINE_MAX_IMAGE_URL_CHARS = 2000
LINE_MAX_POSTBACK_DATA_CHARS = 300

# purpose ของ OutboundMessage (ลง messages.message_type)
PURPOSE_ANSWER = "ai_answer"
PURPOSE_CLARIFY = "ai_clarify"
PURPOSE_CANNOT_ANSWER = "ai_cannot_answer"


def _https_error(value: str, max_chars: int) -> str | None:
    """None ถ้าเป็นลิงก์ https ที่มี host และไม่มีช่องว่าง/อักขระควบคุม ไม่งั้นคืนเหตุผล"""
    if len(value) > max_chars:
        return f"ยาวเกิน {max_chars}"
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return "มีช่องว่างหรืออักขระควบคุม"
    try:
        parts = urlsplit(value)
    except ValueError:
        return "อ่าน URL ไม่ออก"
    if parts.scheme != "https":
        return "ไม่ใช่ https"
    if not parts.hostname:
        return "ไม่มี host"
    return None


def _product_errors(products: Sequence[Product]) -> list[str]:
    """ตรวจข้อมูลสินค้าทุกรายการ (รวมที่เกิน MAX_CAROUSEL_BUBBLES — ข้อมูลผิดจาก API ถือว่าไม่น่าเชื่อทั้งชุด)"""
    errors: list[str] = []
    seen: set[str] = set()
    for i, p in enumerate(products):
        where = f"products[{i}]"
        if not p.sku.strip():
            errors.append(f"{where}.sku: ว่าง")
        elif p.sku in seen:
            errors.append(f"{where}.sku: ซ้ำ")
        else:
            seen.add(p.sku)
            if len(flex_builder.detail_postback_data(p)) > LINE_MAX_POSTBACK_DATA_CHARS:
                errors.append(f"{where}.sku: ยาวเกิน postback data {LINE_MAX_POSTBACK_DATA_CHARS}")
        if not p.name.strip():
            errors.append(f"{where}.name: ว่าง")
        if (reason := _https_error(p.url, LINE_MAX_URI_CHARS)) is not None:
            errors.append(f"{where}.url: {reason}")
        if (
            p.image_url is not None
            and (reason := _https_error(p.image_url, LINE_MAX_IMAGE_URL_CHARS)) is not None
        ):
            errors.append(f"{where}.image_url: {reason}")
        if p.price is not None and (not p.price.is_finite() or p.price < 0):
            errors.append(f"{where}.price: ติดลบหรือไม่ใช่ตัวเลข")
    return errors


def _text_error(name: str, text: str) -> str | None:
    if len(text) > LINE_MAX_TEXT_CHARS:
        return f"{name}: ยาวเกิน {LINE_MAX_TEXT_CHARS} ตัวอักษร"
    return None


def _fail(errors: list[str]) -> ValidationResult:
    return ValidationResult(ok=False, errors=errors)


def validate(
    result: AiResult, route: AiRoute, *, prefix_texts: Sequence[str] = ()
) -> ValidationResult:
    """ตรวจผล AI ตามเส้นทาง แล้วสร้าง OutboundMessage ที่พร้อมส่ง (ฟังก์ชันล้วน)

    - prefix_texts: ข้อความของระบบที่ต้องส่งนำหน้า (เช่น HANDOFF_TIMED_OUT_PREFIX) ใช้กับทุกเส้นทาง
    - ANSWER + products: ตรวจสินค้าทุกรายการ แล้วตัดเหลือ MAX_CAROUSEL_BUBBLES ใบ
      -> [prefix..., intro (ถ้ามี), carousel]
    - ANSWER ไม่มี products: ต้องมี intro (คำตอบ) -> [prefix..., intro]
    - CLARIFY: ต้องมี clarify_question -> [prefix..., clarify_question]
    - OFFER_STAFF: [prefix..., replies.cannot_answer_message()]
    - ESCALATE: ไม่ผ่าน validate (orchestrator ส่งต่อเลย) -> ValueError
    รวมไม่เกิน LINE_MAX_MESSAGES ข้อความ; ไม่ผ่าน -> ok=False + errors (orchestrator ใช้ on_validate_fail)

    การ์ดสร้างจาก result.products เท่านั้น — ไม่อ่านราคา/ลิงก์/SKU จากข้อความอิสระของ AI (intro)
    intro ส่งตามที่ AI เขียน ตรวจแค่ความยาว (ข้อเสนอ: ตรวจลิงก์/ราคาใน intro ด้วย — ยังไม่ได้ตัดสิน)
    """
    if route is AiRoute.ESCALATE:
        raise ValueError("route ESCALATE ไม่ต้อง validate — orchestrator ส่งต่อพนักงาน")

    errors: list[str] = []
    messages: list[JsonDict] = []

    for i, t in enumerate(prefix_texts):
        if not t.strip():
            errors.append(f"prefix_texts[{i}]: ว่าง")
        elif (e := _text_error(f"prefix_texts[{i}]", t)) is not None:
            errors.append(e)
        else:
            messages.append(replies.text_message(t))

    intro = (result.intro or "").strip()

    if route is AiRoute.ANSWER:
        purpose = PURPOSE_ANSWER
        if result.products:
            errors.extend(_product_errors(result.products))
            if intro:
                if (e := _text_error("intro", intro)) is not None:
                    errors.append(e)
                else:
                    messages.append(replies.text_message(intro))
            if not errors:
                cards = list(result.products)[:MAX_CAROUSEL_BUBBLES]
                messages.append(flex_builder.product_carousel(cards))
        elif not intro:
            errors.append("intro: คำตอบว่าง (ไม่มีทั้ง products และ intro)")
        elif (e := _text_error("intro", intro)) is not None:
            errors.append(e)
        else:
            messages.append(replies.text_message(intro))
    elif route is AiRoute.CLARIFY:
        purpose = PURPOSE_CLARIFY
        question = (result.clarify_question or "").strip()
        if not question:
            errors.append("clarify_question: ว่าง")
        elif (e := _text_error("clarify_question", question)) is not None:
            errors.append(e)
        else:
            messages.append(replies.text_message(question))
    elif route is AiRoute.OFFER_STAFF:
        purpose = PURPOSE_CANNOT_ANSWER
        messages.append(replies.cannot_answer_message())
    else:  # pragma: no cover - AiRoute ใหม่ที่ยังไม่รองรับ
        raise ValueError(f"ไม่รู้จัก route {route}")

    if len(messages) > LINE_MAX_MESSAGES:
        errors.append(f"messages: เกิน {LINE_MAX_MESSAGES} ข้อความ ({len(messages)})")
    if errors:
        return _fail(errors)
    return ValidationResult(ok=True, outbound=OutboundMessage(messages=messages, purpose=purpose))


def on_validate_fail(counters: Counters) -> tuple[bool, Counters]:
    """validate ไม่ผ่าน: validate_fail_streak+1 -> (ส่งต่อไหม, counters ใหม่)

    ครั้งแรก -> (False, ...) orchestrator ตอบ VALIDATE_RETRY; ติดกันครั้งที่ 2 -> (True, ...) ส่งต่อพนักงาน
    ส่งต่อแล้วล้าง streak (เริ่มนับใหม่เมื่อกลับมาคุยกับ AI)
    """
    streak = counters.validate_fail_streak + 1
    if streak >= VALIDATE_FAIL_ESCALATE_AT:
        return True, counters.model_copy(update={"validate_fail_streak": 0})
    return False, counters.model_copy(update={"validate_fail_streak": streak})


def reset_validate_streak(counters: Counters) -> Counters:
    """validate ผ่าน -> ล้าง validate_fail_streak"""
    if counters.validate_fail_streak == 0:
        return counters
    return counters.model_copy(update={"validate_fail_streak": 0})
