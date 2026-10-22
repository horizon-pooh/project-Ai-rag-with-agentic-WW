"""app/config.py

อ่าน env ทั้งหมดที่จุดเดียว ครั้งเดียวตอนเริ่ม ขาดค่าบังคับเมื่อไหร่หยุดทันที (fail fast)

รายละเอียด:
 - ใช้ pydantic-settings อ่านจาก .env (รายการตัวแปรดู .env.example)
 - บังคับ: LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, DATABASE_URL, REDIS_URL
 - ห้ามข้ามการตรวจ signature ไม่ว่าโหมดไหน
 - AI_TIMEOUT_S, AI_CONCURRENCY, TURN_DEADLINE_S: ค่าจริงรอคำตอบเรื่อง AI API
 - STATE_TTL_HOURS=2, HANDOFF_TTL_HOURS=6
 - token / key อยู่ที่นี่ที่เดียว ห้ามส่งเข้า AI API ห้ามลง log

แนวคิดจาก: src/config/index.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
