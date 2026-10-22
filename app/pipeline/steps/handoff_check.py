"""app/pipeline/steps/handoff_check.py

ขั้น 4: ดูคำสั่งคืน AI / ขอพนักงาน แล้วดูสถานะ คืน HandoffResult

รายละเอียด:
 - ลำดับ (สำคัญ):
   1. human และ now - handoff_started_at >= 6 ชม. -> end_handoff(timeout) แล้วเช็คต่อ (ตาข่ายของ cron)
   2. คำสั่ง 'คุยกับ AI' (postback action=resume_ai หรือพิมพ์ตรงทั้งข้อความหลัง normalize ตาม prompts/rules/resume_ai.txt): human -> end_handoff(user_resumed) -> RESUMED; bot -> ALREADY_BOT
   3. postback action=talk_to_staff: bot -> ESCALATE; human -> SILENT
   4. human -> SILENT (บันทึกแต่ไม่ตอบ)
   5. อื่นๆ -> CONTINUE (+ timed_out ถ้าเพิ่งหมดเวลา; เสนอ: นำหน้าคำตอบด้วย 'หมดเวลาคุยกับพนักงานแล้ว')
 - ข้อ 2 ต้องก่อนข้อ 4 ไม่งั้นคำสั่งถูกกลืน
 - normalize: NFC, ลบ zero-width, ลบช่องว่างทั้งหมด, casefold
 - 'คุยกับ AI หน่อย หาตู้เย็น' ไม่นับเป็นคำสั่ง (ตั้งใจ)
 - kill switch ไม่เช็คที่นี่ (เช็คที่ send)
 - ถ้า fast_path ทำไปแล้ว ต้องไม่ทำซ้ำ

แนวคิดจาก: src/conversation/handleMessage.js, src/conversation/stateMachine.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
