"""app/pipeline/steps/update_state.py

ขั้น 7: อัปเดต conversation_state หลังได้ AiResult

รายละเอียด:
 - API คืน state_out -> ใช้ state_out
 - ไม่คืน -> วิธีสำรอง (เช่นส่งแชทย้อนหลังแทน Delta) รอคำตอบเรื่อง AI API ข้อ 3
 - ต่ออายุ state 2 ชม. (STATE_TTL_HOURS) ทุกครั้งที่ user พิมพ์ในโหมด bot
 - state เก็บหมวด, filter, SKU ล่าสุด; ไม่เก็บรูป
 - transaction สั้นเฉพาะตอนอ่านแล้วเขียน

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว (next_state, state_expiry เป็นฟังก์ชันล้วน) — orchestrator (งาน 8) เป็นคนเรียก StateRepo.save
"""

from __future__ import annotations

from datetime import datetime

from app.config import Settings
from app.pipeline.schemas import AiResult, Counters, JsonDict


def next_state(
    prev_state: JsonDict | None, result: AiResult, counters: Counters
) -> tuple[JsonDict | None, Counters]:
    """คำนวณ (state ใหม่, counters ใหม่) จากผล AI (ฟังก์ชันล้วน ไม่แก้ค่าที่รับมา)

    - result.state_out ไม่ใช่ None -> ใช้ state_out (แม้เป็น {} = API ล้าง state)
    - state_out เป็น None -> คง prev_state ไว้
      รอคำตอบ ข้อ 3: API คืน state หรือไม่ — ถ้าไม่คืน วิธีสำรองคือส่งแชทย้อนหลัง (redact) แทน Delta
      ซึ่ง call_ai ส่ง recent_messages อยู่แล้ว ที่นี่จึงไม่สร้าง state เอง
    - result.products ไม่ว่าง -> counters.last_products = products (ใช้ตอบปุ่ม "ดูรายละเอียด")
      ว่าง -> คงรายการเดิม (คำตอบแบบ answer/clarify ไม่ลบการ์ดที่ลูกค้าอาจกดอยู่)
    """
    state = dict(result.state_out) if result.state_out is not None else prev_state
    if result.products:
        counters = counters.model_copy(update={"last_products": list(result.products)})
    return state, counters


def state_expiry(now: datetime, settings: Settings) -> datetime:
    """เวลาหมดอายุของ state: now + STATE_TTL_HOURS (ต่ออายุทุกครั้งที่ user พิมพ์ในโหมด bot)"""
    return now + settings.state_ttl
