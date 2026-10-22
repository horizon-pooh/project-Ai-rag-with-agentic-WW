"""app/worker/cron.py

งานเบื้องหลังตามเวลา

รายละเอียด:
 - requeue_pending (ทุก 1-2 นาที): หา line_raw_events ที่ค้าง pending นานเกินควร ใส่คิวใหม่ด้วย job id เดิม; จำนวนที่เจอใช้แจ้งเตือนได้
 - handoff_timeout (ทุกไม่กี่นาที): conversation ที่ human และ now - handoff_started_at >= HANDOFF_TTL_HOURS -> end_handoff(reason='timeout')
 - handoff_check ในขั้น 4 เช็คซ้ำเป็นตาข่าย ทั้งสองทางใช้ end_handoff ตัวเดียว (ไม่ส่งซ้ำ)
 - retention (วันละครั้ง): raw 90 วัน, ข้อความและ trace 12 เดือนแล้ว anonymize, รูปเก็บสั้นกว่า (ลบใน MinIO แยก)
 - (เสนอ) รายงานรายวัน ยังไม่อยู่ในรอบแรก

แนวคิดจาก: src/jobs/, src/ops/dailyReport.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
