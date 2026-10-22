"""app/repositories/raw_events.py

ตาราง line_raw_events (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - insert แบบกันซ้ำด้วย UNIQUE webhook_event_id (ตัด replyToken ออก)
 - process_status: pending -> processing -> done / failed
 - ดึง event ค้างของ user ตามลำดับเวลา
 - หา pending ค้างนาน (ให้ cron requeue_pending)
 - ลบเกิน 90 วัน (retention)

แนวคิดจาก: src/pipeline/dedupe.js, src/store/redisStore.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
