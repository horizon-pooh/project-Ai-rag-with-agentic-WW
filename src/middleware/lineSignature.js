/**
 * src/middleware/lineSignature.js
 *
 * ตรวจ x-line-signature (HMAC-SHA256 ของ raw body) ไม่ผ่านตอบ 401
 *
 * รายละเอียด:
 *  - ขึ้นกับคำตอบว่าใครเป็นเจ้าของ webhook URL (docs/open-questions.md ข้อ 1)
 *
 * ย้ายมาจาก MVP: line_/server.js (verify signature)
 * สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
 */
