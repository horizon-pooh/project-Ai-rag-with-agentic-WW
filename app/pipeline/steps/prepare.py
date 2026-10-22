"""app/pipeline/steps/prepare.py

ขั้น 3: เตรียมข้อมูลก่อนตัดสินอะไร

รายละเอียด:
 - หา/สร้าง line_users และ conversation (bot เงียบเกิน 2 ชม. -> conversation ใหม่; human ห้ามเปิดใหม่)
 - บันทึกข้อความขาเข้า (messages)
 - รูป -> ดึงจาก LINE เก็บ MinIO; ระหว่าง human เก็บแต่ไม่ส่ง AI
 - เช็ค consent (line_consents)
 - สร้าง ai_runs
 - อ่าน conversation_state ที่ยังไม่หมดอายุ (หมดแล้ว -> state_expired)
 - ข้อความระหว่าง human ไม่ต่ออายุ state ไม่เลื่อน 6 ชม.

แนวคิดจาก: src/conversation/handleMessage.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
