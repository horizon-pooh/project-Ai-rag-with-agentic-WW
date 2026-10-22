"""app/repositories/conversations.py

ตาราง conversations (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - หา conversation ที่เปิดอยู่ / เปิดใหม่ (bot เงียบเกิน 2 ชม.; human ห้ามเปิดใหม่)
 - start_handoff: UPDATE ... SET status='human', handoff_started_at=now(), handoff_reason WHERE status='bot'
 - end_handoff: UPDATE ... SET status='bot', handoff_ended_at=now(), handoff_end_reason=$2 WHERE id=$1 AND status='human' AND handoff_started_at=$3 RETURNING *
 - หา human ที่เกิน HANDOFF_TTL_HOURS (ให้ cron handoff_timeout)
 - token ลิงก์ /close (ใช้ครั้งเดียว)
 - outcome, outcome_source, staff_label (ข้อเสนอ หัวข้อ นิยามความสำเร็จและการวัด)

แนวคิดจาก: src/store/redisStore.js, src/conversation/stateMachine.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
