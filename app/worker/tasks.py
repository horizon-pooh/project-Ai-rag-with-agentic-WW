"""app/worker/tasks.py

process_event(raw_event_id): งานหลักหนึ่งชิ้นต่อหนึ่ง event

รายละเอียด:
 - จับล็อกต่อ user (locks.py) ก่อนทำอะไร
 - ทำ event ที่ค้างของ user ตามลำดับเวลา (เก่าสุดก่อน)
 - ตรวจ process_status กันรันซ้ำ (คิวรันซ้ำได้เมื่อ retry)
 - สร้างตัวช่วย (repositories, services) แล้วส่งให้ pipeline.orchestrator
 - ตั้ง raw_event_id / ai_run_id ใน log context
 - จบแล้วตั้ง process_status = done หรือ failed (บันทึก error เสมอ)

แนวคิดจาก: src/jobs/softnixWorker.js, src/pipeline/userQueue.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
