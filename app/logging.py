"""app/logging.py

ตั้ง structlog ให้ log เป็น JSON และติด id ตามย้อนได้ทั้งสาย

รายละเอียด:
 - ทุก log ในงานหนึ่งติด raw_event_id และ ai_run_id (ผ่าน contextvars)
 - id ชุดเดียวกันอยู่ใน ai_steps และข้อความ error ที่ส่ง Sentry
 - ผ่าน privacy/redact ก่อนเขียน: ห้ามมีเบอร์โทร, เลขบัตร, token, replyToken
 - ระดับ log จาก LOG_LEVEL

แนวคิดจาก: src/observability/logger.js, src/observability/redact.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
