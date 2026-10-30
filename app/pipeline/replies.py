"""app/pipeline/replies.py

ข้อความถึงลูกค้าทั้งหมด (ค่าคงที่ที่เดียว) และตัวช่วยสร้างข้อความ text ของ LINE พร้อม quick reply

รายละเอียด:
 - ข้อความตรงตามตารางใน docs/PLAN.md หัวข้อ ข้อความถึงลูกค้า (แก้ที่นี่ที่เดียว)
 - quick reply: "คุยกับเจ้าหน้าที่" -> action=talk_to_staff, "เริ่มใหม่" -> action=restart (postback)
 - ที่นี่สร้างเฉพาะข้อความ text; การ์ดสินค้า (flex carousel) อยู่ที่ services/flex_builder.py
 - ผลเป็น dict ของ LINE message object; ส่งจริงผ่าน steps/send.py (sendGuard) เท่านั้น

แนวคิดจาก: ใหม่ (docs/PLAN.md)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

from collections.abc import Sequence

from app.pipeline.schemas import (
    LINE_MAX_QUICK_REPLY_ITEMS,
    JsonDict,
    OutboundMessage,
    PostbackAction,
    postback_data,
)

# --- ข้อความ (docs/PLAN.md) ---
RESUMED = "กลับมาคุยกับผู้ช่วย AI แล้วค่ะ ต้องการหาสินค้าอะไรคะ"
ALREADY_BOT = "ตอนนี้กำลังคุยกับผู้ช่วย AI อยู่แล้วค่ะ ต้องการหาสินค้าอะไรคะ"
HANDOFF_IN_HOURS = "รับเรื่องแล้วค่ะ กำลังส่งต่อให้เจ้าหน้าที่ เจ้าหน้าที่จะตอบกลับในแชทนี้ค่ะ"
HANDOFF_OUT_OF_HOURS = (
    "รับเรื่องแล้วค่ะ ตอนนี้อยู่นอกเวลาทำการ ({hours}) เจ้าหน้าที่จะตอบกลับในเวลาทำการค่ะ"
)
HANDOFF_TIMED_OUT_PREFIX = "หมดเวลาคุยกับเจ้าหน้าที่แล้ว ตอนนี้กลับมาคุยกับผู้ช่วย AI ค่ะ"
STATE_EXPIRED = "ข้อมูลที่คุยไว้ก่อนหน้าหมดอายุแล้วค่ะ ช่วยบอกอีกครั้งว่าต้องการหาสินค้าอะไรคะ"
RESTARTED = "เริ่มใหม่แล้วค่ะ ต้องการหาสินค้าอะไรคะ"
AI_DOWN = "ขออภัยค่ะ ระบบตอบกลับขัดข้องชั่วคราว"
CANNOT_ANSWER = "ขออภัยค่ะ เรื่องนี้ยังตอบให้ไม่ได้ ต้องการคุยกับเจ้าหน้าที่ไหมคะ"
VALIDATE_RETRY = "ขอโทษค่ะ ช่วยบอกรายละเอียดเพิ่มอีกนิดได้ไหมคะ เช่น ประเภทหรือรุ่นสินค้า"
SEARCHING = "กำลังค้นหาให้ค่ะ รอสักครู่นะคะ"
RATE_LIMITED = "ได้รับข้อความแล้วค่ะ ขอเวลาสักครู่นะคะ"
EMPTY = "ต้องการให้ช่วยหาสินค้าอะไรคะ"
IMAGE_UNREADABLE = "ได้รับรูปแล้วค่ะ แต่ตอนนี้ยังอ่านรูปไม่ได้ ช่วยพิมพ์ชื่อหรือรุ่นสินค้าที่ต้องการให้หน่อยนะคะ"
DETAIL_NOT_FOUND = "ขออภัยค่ะ ไม่พบรายละเอียดสินค้านี้แล้ว ช่วยค้นหาใหม่อีกครั้งนะคะ"

# --- ป้ายปุ่ม quick reply (LINE จำกัด label 20 ตัวอักษร) ---
LABEL_TALK_TO_STAFF = "คุยกับเจ้าหน้าที่"
LABEL_RESTART = "เริ่มใหม่"


def postback_item(label: str, data: str) -> JsonDict:
    """quick reply item แบบ postback; displayText = label (ลูกค้าเห็นว่ากดอะไรในแชท)"""
    return {
        "type": "action",
        "action": {"type": "postback", "label": label, "data": data, "displayText": label},
    }


def talk_to_staff_item() -> JsonDict:
    return postback_item(LABEL_TALK_TO_STAFF, postback_data(PostbackAction.TALK_TO_STAFF))


def restart_item() -> JsonDict:
    return postback_item(LABEL_RESTART, postback_data(PostbackAction.RESTART))


def text_message(text: str, quick_replies: Sequence[JsonDict] = ()) -> JsonDict:
    """LINE text message (+ quickReply ถ้ามี) — quick reply เกิน 13 รายการ -> ValueError"""
    if len(quick_replies) > LINE_MAX_QUICK_REPLY_ITEMS:
        raise ValueError(f"quick reply เกิน {LINE_MAX_QUICK_REPLY_ITEMS} รายการ")
    msg: JsonDict = {"type": "text", "text": text}
    if quick_replies:
        msg["quickReply"] = {"items": list(quick_replies)}
    return msg


def handoff_text(*, in_hours: bool, hours: str | None) -> str:
    """ข้อความตอนส่งต่อ: นอกเวลาทำการและรู้เวลาทำการ -> HANDOFF_OUT_OF_HOURS ไม่งั้น HANDOFF_IN_HOURS"""
    if not in_hours and hours:
        return HANDOFF_OUT_OF_HOURS.format(hours=hours)
    return HANDOFF_IN_HOURS


def state_expired_message() -> JsonDict:
    return text_message(STATE_EXPIRED, [restart_item()])


def ai_down_message() -> JsonDict:
    return text_message(AI_DOWN, [talk_to_staff_item()])


def cannot_answer_message() -> JsonDict:
    return text_message(CANNOT_ANSWER, [talk_to_staff_item()])


def outbound_text(purpose: str, *texts: str) -> OutboundMessage:
    """OutboundMessage จากข้อความ text ล้วน (ไม่มี quick reply) เช่น outbound_text("resumed", RESUMED)"""
    return OutboundMessage(messages=[text_message(t) for t in texts], purpose=purpose)
