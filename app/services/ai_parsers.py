"""app/services/ai_parsers.py

แปลงคำตอบดิบของ AI API เป็น AiResult

รายละเอียด:
 - JsonParser: ถ้า API ตอบเป็น JSON
 - MarkdownParser: ถ้าตอบเป็น markdown (แบบ softnixParser เดิม)
 - เลือกตัวไหน: รอคำตอบเรื่อง AI API ข้อ 2
 - markdown ต้องมีเคสทดสอบจากคำตอบจริง 20-30 ตัวอย่าง
 - อ่านไม่ออก -> AiBadResponse (ไม่เดา)
 - เก็บ raw ตัดความยาวไว้ใน AiResult เพื่อ debug

แนวคิดจาก: src/ai/softnixParser.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
