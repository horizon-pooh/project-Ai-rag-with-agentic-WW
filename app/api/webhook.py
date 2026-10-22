"""app/api/webhook.py

POST /webhook: รับ event จาก LINE บันทึกก่อน แล้วใส่คิว ตอบ 200 ทันที

รายละเอียด:
 - ตรวจ signature จาก body ดิบ (X-Line-Signature) ไม่ผ่าน -> 401
 - บันทึก line_raw_events ก่อนตอบ 200 เสมอ (ตัด replyToken ออกจากแถว)
 - กันซ้ำด้วย UNIQUE webhook_event_id (ซ้ำ -> ข้าม ตอบ 200)
 - ใส่คิว arq โดยใช้ raw id เป็น job id; replyToken เก็บใน Redis กับงานเท่านั้น
 - คำสั่งเปลี่ยนสถานะ (talk_to_staff, resume_ai, ขอคนชัดเจน) ส่งต่อให้ api/fast_path ด้วย
 - ใส่คิวพลาด -> แถวยังเป็น pending, cron requeue_pending เก็บกวาด

แนวคิดจาก: src/routes/webhook.js, src/middleware/lineSignature.js, src/pipeline/dedupe.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
