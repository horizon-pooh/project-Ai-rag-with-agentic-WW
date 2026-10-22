"""tests/services/test_ai_client.py

เคสทดสอบของ app/services/ai_client.py และ ai_parsers.py (จำลอง HTTP ด้วย respx, ยังไม่มีฟังก์ชันทดสอบ)

รายละเอียด (สิ่งที่ API ทำ -> ผลที่คาด):
 - 200 + คำตอบถูกรูปแบบ -> AiResult (kind ถูก, products ครบ sku/name/price/url/image_url)
 - timeout ครั้งแรก แล้วครั้งที่สองสำเร็จ -> AiResult, attempts = 2
 - timeout ทั้งสองครั้ง -> AiUnavailable
 - ต่อไม่ได้ (connection error) -> ลองซ้ำ 1 ครั้ง
 - 429 มี retry-after และเวลาเหลือพอ -> รอตาม retry-after แล้วลองซ้ำ
 - 429 / 5xx แต่เวลาเหลือไม่พอ (ใกล้ deadline) -> ไม่ลองซ้ำ -> AiUnavailable
 - 5xx ครั้งเดียวแล้วสำเร็จ -> AiResult
 - 400 / 401 / 4xx อื่น -> ไม่ลองซ้ำ -> AiBadResponse
 - 200 แต่อ่านไม่ออก -> ไม่ลองซ้ำ -> AiBadResponse
 - รวมทุกรอบไม่เกิน deadline
 - Semaphore: งานพร้อมกันไม่เกิน AI_CONCURRENCY
 - request ที่ส่งออกไม่มีข้อความดิบ ไม่มี token LINE (มีแค่ text_redacted)
 - MarkdownParser (ถ้าใช้): 20-30 ตัวอย่างจากคำตอบจริง (รอคำตอบเรื่อง AI API ข้อ 2)

แนวคิดจาก: src/ai/aiClient.js, src/ai/softnixParser.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
