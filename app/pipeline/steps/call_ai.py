"""app/pipeline/steps/call_ai.py

ขั้น 6: เรียก AI API ครั้งเดียว เห็นแค่ AiRequest / AiResult

รายละเอียด:
 - สร้าง AiRequest จาก TurnContext (ข้อความ redact แล้วเท่านั้น)
 - เรียก services/ai_client ส่ง deadline ที่เหลือของข้อความนี้ไปด้วย
 - ไม่รู้จักรูปแบบ HTTP ของ API (อยู่ใน ai_client ที่เดียว)
 - ลองซ้ำ: timeout/ต่อไม่ได้/429/5xx -> ซ้ำ 1 ครั้งถ้าเวลาเหลือพอ (เคารพ retry-after); 4xx อื่น/อ่านไม่ออก -> ไม่ซ้ำ
 - รวมทุกรอบไม่เกิน deadline (เพดานเวลารวมต่อข้อความ)
 - AiUnavailable / AiBadResponse ส่งต่อให้ orchestrator -> fallback
 - ระหว่างรอ: เร็ว -> loading animation + reply; เกินอายุ replyToken -> reply 'กำลังค้นหา' แล้ว push; ช้ามาก -> บอกเวลารอ / เลิกรอแล้วส่งต่อ (รอคำตอบเรื่องความเร็ว API)
 - ไม่ทำ agent ไม่ทำ tool-calling ไม่เรียก Search API เอง

แนวคิดจาก: src/ai/aiClient.js, src/jobs/softnixQueue.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""
