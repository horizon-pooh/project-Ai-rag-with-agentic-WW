"""app/services/ai_client.py

client ของ AI API ภายนอก: ที่เดียวที่รู้จักรูปแบบ API

รายละเอียด:
 - รับ AiRequest คืน AiResult (ใช้ ai_parsers แปลงผล)
 - httpx async + timeout (AI_TIMEOUT_S) ทุกครั้ง
 - asyncio.Semaphore(AI_CONCURRENCY) จำกัดงานพร้อมกันตามที่ API รับไหว
 - ลองซ้ำ: timeout/ต่อไม่ได้/429/5xx -> 1 ครั้งถ้าเวลาเหลือพอ (เคารพ retry-after); 4xx อื่น/อ่านไม่ออก -> ไม่ซ้ำ
 - รวมทุกรอบไม่เกิน deadline ที่ส่งเข้ามา
 - error: AiUnavailable (ลองแล้วยังพัง), AiBadResponse (อ่านไม่ออก/4xx)
 - ส่งเฉพาะข้อความ redact แล้ว
 - รอคำตอบ: API คือ Softnix หรือไม่, JSON/markdown, คืน state หรือไม่, รับรูปได้หรือไม่

แนวคิดจาก: src/ai/aiClient.js, src/ai/llmClient.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
