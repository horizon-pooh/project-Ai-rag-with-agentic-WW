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

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
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

# คำเตือน (อยู่ใน ValidationResult.errors แต่ ok=True — ไม่ทำให้ validate ไม่ผ่าน ลง ai_steps ให้ดูย้อนหลัง)
WARN_INTRO_PRICE_MISMATCH = "intro_price_mismatch"
WARN_INTRO_URL_REMOVED = "intro_url_removed"
WARN_CLARIFY_URL_REMOVED = "clarify_question_url_removed"

# --- ตรวจข้อความอิสระของ AI (intro / clarify_question) — Line_ai_bot.md §10 ห้ามเชื่อราคา/ลิงก์ที่ AI เขียน ---
# ภาษาไทยไม่เว้นวรรค ("ดูได้ที่https://x.example/aคะ") จึงให้ URL มีได้แค่อักขระ ASCII ของ URL
# (RFC 3986 + %) — ตัวอักษรไทยที่ติดท้ายไม่นับเป็นส่วนของ URL
_URL_CHARS = r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]"
_URL_RE = re.compile(
    rf"(?:https?://|www\.){_URL_CHARS}+"
    # โดเมนเปล่า เช่น shop.example.com/xxx: label สุดท้ายต้องเป็นตัวอักษร ≥2 ตัว
    # กันตัวเลขอย่าง "9.1 คิว" / "1.5" และไม่มีจุดก็ไม่นับ (รุ่นสินค้า RT38K5032S8)
    rf"|(?<![A-Za-z0-9@.\-])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{{2,}}(?![A-Za-z0-9-])(?:/{_URL_CHARS}*)?",
    re.IGNORECASE,
)
# เครื่องหมายท้ายประโยคที่ติดมากับ URL ไม่นับเป็นส่วนของ URL
_URL_TRAILING_PUNCT = ".,;:!?)]}'\""

# ตัวเลขราคา: 1,234 / 1234 / 1,234.50 — ต้องไม่ติดตัวเลข/ตัวอักษรอังกฤษด้านหน้า (กันรุ่นสินค้า)
_NUM = r"(?<![\d,.A-Za-z])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d|,\d)"
# รูปแบบราคา: ฿1,234 / ราคา 12990 / ราคา฿12,990 / 1,234 บาท / 1234บาท / 1,234.-
# ตัวเลขที่ไม่มีคำบอกราคา (ขนาด "9.1 คิว", "2 ประตู", ปี) ไม่นับ
# ข้อจำกัด: "ลด 500 บาท" / "ค่าส่ง 100 บาท" ก็นับเป็นราคา (ไม่ตรงสินค้า -> ตัด intro ทั้งข้อความ)
_PRICE_RE = re.compile(rf"(?:ราคา\s*(?:฿\s*)?|฿\s*)(?P<a>{_NUM})|(?P<b>{_NUM})\s*(?:บาท|\.-)")


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


def _strip_unknown_urls(text: str, products: Sequence[Product]) -> tuple[str, bool]:
    """ลบ URL ที่ไม่ตรงกับ product.url ตัวไหนเลย (ตรงทุกตัวอักษร หลังตัดเครื่องหมายท้าย) -> (ข้อความ, ลบไหม)

    ลิงก์ที่ AI แต่งเอง (หรือถูกหลอกให้ใส่) อาจพาลูกค้าไปเว็บอื่น — ลบทิ้งแต่ไม่ทำให้ validate ไม่ผ่าน
    """
    allowed = {p.url for p in products}
    removed = False
    pieces: list[str] = []
    last = 0
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(_URL_TRAILING_PUNCT)
        if not url or url in allowed:
            continue
        pieces.append(text[last : m.start()])
        last = m.start() + len(url)  # เครื่องหมายท้ายยังอยู่ในข้อความ
        removed = True
    if not removed:
        return text, False
    pieces.append(text[last:])
    out = "".join(pieces)
    out = re.sub(r"[^\S\n]{2,}", " ", out)  # ช่องว่างที่เหลือจากการลบ (ไม่ยุบบรรทัด)
    out = re.sub(r"[^\S\n]+\n", "\n", out)
    return out.strip(), True


def _text_prices(text: str) -> list[Decimal]:
    prices: list[Decimal] = []
    for m in _PRICE_RE.finditer(text):
        raw = (m.group("a") or m.group("b")).replace(",", "")
        try:
            prices.append(Decimal(raw))
        except InvalidOperation:  # pragma: no cover - regex รับแต่ตัวเลข
            continue
    return prices


def _prices_match(text: str, products: Sequence[Product]) -> bool:
    """ทุกราคาในข้อความต้องเท่ากับราคาของสินค้าสักตัว (เทียบ Decimal: 12,990 == 12990.00)"""
    known = {p.price for p in products if p.price is not None}
    return all(price in known for price in _text_prices(text))


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

    การ์ดสร้างจาก result.products เท่านั้น — ไม่อ่านราคา/ลิงก์/SKU จากข้อความอิสระของ AI
    ข้อความอิสระ (intro, clarify_question) ตรวจก่อนส่ง:
    - ลิงก์: เก็บเฉพาะที่ตรงกับ product.url ทุกตัวอักษร นอกนั้นลบออกจากข้อความ (ไม่ทำให้ไม่ผ่าน)
    - ราคาใน intro เมื่อมี products: ทุกราคาต้องตรงกับราคาสินค้าสักตัว ไม่ตรง -> ตัด intro ทั้งข้อความ
      (การ์ดยังมีราคาที่ถูกต้อง) + คำเตือน intro_price_mismatch
    - ไม่มี products: ราคาในข้อความส่งตามเดิม — รอคำตอบ: ราคาที่ AI ตอบผิดใครรับผิดชอบ ต้องมีข้อความกำกับไหม
      (Line_ai_bot.md เรื่องค้าง)
    - รหัสสินค้า (SKU) ในข้อความไม่ตรวจ: รูปแบบไม่แน่นอน แยกจากรุ่นสินค้า/ตัวเลขอื่นไม่ได้แน่ชัด
      ตรวจแล้วจะตัดข้อความดีทิ้งเยอะ; การ์ดและปุ่ม detail ใช้ SKU จาก products อยู่แล้ว
    คำเตือนอยู่ใน errors ทั้งที่ ok=True (ValidationResult อนุญาต) — orchestrator ลง ai_steps
    """
    if route is AiRoute.ESCALATE:
        raise ValueError("route ESCALATE ไม่ต้อง validate — orchestrator ส่งต่อพนักงาน")

    errors: list[str] = []
    warnings: list[str] = []
    messages: list[JsonDict] = []

    for i, t in enumerate(prefix_texts):
        if not t.strip():
            errors.append(f"prefix_texts[{i}]: ว่าง")
        elif (e := _text_error(f"prefix_texts[{i}]", t)) is not None:
            errors.append(e)
        else:
            messages.append(replies.text_message(t))

    if route is AiRoute.ANSWER:
        purpose = PURPOSE_ANSWER
        intro = (result.intro or "").strip()
        if intro:
            intro, url_removed = _strip_unknown_urls(intro, result.products)
            if url_removed:
                warnings.append(WARN_INTRO_URL_REMOVED)
        if result.products:
            if intro and not _prices_match(intro, result.products):
                warnings.append(WARN_INTRO_PRICE_MISMATCH)
                intro = ""
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
        if question:
            question, url_removed = _strip_unknown_urls(question, result.products)
            if url_removed:
                warnings.append(WARN_CLARIFY_URL_REMOVED)
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
        return _fail(errors + warnings)
    return ValidationResult(
        ok=True, outbound=OutboundMessage(messages=messages, purpose=purpose), errors=warnings
    )


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
