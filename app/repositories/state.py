"""app/repositories/state.py

ตาราง conversation_state (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - อ่านเฉพาะแถวที่ยังไม่หมดอายุ (expires_at > now())
 - เขียน/ต่ออายุ STATE_TTL_HOURS (2 ชม.)
 - ล้างเมื่อ end_handoff สำเร็จ
 - ไม่ต่ออายุระหว่าง human

แนวคิดจาก: src/store/redisStore.js (แบบเก่า: session 24 ชม. ล้าสมัย)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
