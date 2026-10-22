"""app/repositories/ai_runs.py

ตาราง ai_runs และ ai_steps (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - สร้าง ai_runs ต่อหนึ่งข้อความ (ผูก raw_event_id)
 - ai_steps (orchestrator เป็นคนเรียก): input redact (รูปเก็บแค่ id), output + raw ตัดแล้ว, latency_ms, attempts, error_class, cost (ถ้ามี), raw_event_id, ai_run_id
 - ใช้ทำเวลาตอบ p95 และต้นทุนต่อบทสนทนา
 - anonymize เมื่อครบ 12 เดือน

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
