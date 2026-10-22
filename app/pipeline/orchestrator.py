"""app/pipeline/orchestrator.py

ตัวประสานงาน: เรียก step ตามลำดับ และบันทึก ai_steps ทุกขั้น

รายละเอียด:
 - ลำดับ: prepare -> handoff_check -> rules -> call_ai -> update_state -> validate -> send
 - handoff_check ได้ RESUMED / ALREADY_BOT / ESCALATE / SILENT -> ข้ามขั้นที่ไม่ต้องใช้
 - rules ตัดสินส่งต่อ/ตอบเอง -> ไม่เรียก AI
 - AiUnavailable / AiBadResponse -> fallback
 - บันทึก ai_steps เอง (step ไม่บันทึกเอง): input redact, output, latency_ms, attempts, error_class, raw_event_id, ai_run_id
 - คุมเพดานเวลารวมต่อข้อความ (TURN_DEADLINE_S)
 - ค่าเริ่มต้นเมื่อพลาดต้องปลอดภัย: ถามกลับหรือส่งต่อ ไม่ไปต่อเงียบๆ

แนวคิดจาก: src/conversation/handleMessage.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
