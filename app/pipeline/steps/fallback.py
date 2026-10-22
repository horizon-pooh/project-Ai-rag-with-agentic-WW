"""app/pipeline/steps/fallback.py

คำตอบสำรองเมื่อ AI ล่มหรืออ่านผลไม่ออก

รายละเอียด:
 - เรียกโดย orchestrator เมื่อได้ AiUnavailable / AiBadResponse
 - ข้อความเข้าข่ายเสี่ยงตามกฎขั้น 5 -> ส่งต่อพนักงาน
 - ไม่เข้าข่าย -> ขอโทษ + ปุ่มคุยกับพนักงาน
 - บันทึก error เสมอ (error_class ใน ai_steps)
 - ส่งผ่าน send (sendGuard) เหมือนข้อความอื่น

แนวคิดจาก: src/conversation/escalate.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
