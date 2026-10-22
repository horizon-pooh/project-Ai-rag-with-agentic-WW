"""app/pipeline/schemas.py

ชนิดข้อมูล (Pydantic v2) ที่ step รับและคืน

รายละเอียด:
 - TurnContext: raw_event_id, ai_run_id, user, conversation, ข้อความขาเข้า, state, flags
 - HandoffResult: CONTINUE / RESUMED / ALREADY_BOT / ESCALATE / SILENT + timed_out
 - AiRequest: state (None ถ้าหมดอายุ/เพิ่งล้าง), text_redacted, ข้อความล่าสุด 2-4 (redact), รูปที่ย่อแล้ว (ถ้า API รับ), flags (state_expired, just_resumed, handoff_timed_out)
 - AiResult: kind (products/answer/clarify/cannot_answer), intro, products, clarify_question, escalate_hint, state_out, raw (ตัดความยาว), latency_ms
 - Product: sku, name, price, url, image_url (มาจาก API เท่านั้น AI ห้ามแต่งเอง)
 - OutboundMessage: ชนิดข้อความ LINE ที่ผ่าน validate แล้ว
 - ฟิลด์ของ AiRequest/AiResult ขึ้นกับรูปแบบ AI API (รอคำตอบ)

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
