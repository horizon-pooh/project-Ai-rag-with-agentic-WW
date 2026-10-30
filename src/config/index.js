/**
 * src/config/index.js
 *
 * อ่าน env ทั้งหมดที่จุดเดียว ตรวจค่าบังคับ ขาดเมื่อไหร่หยุดทันที
 *
 * รายละเอียด:
 *  - production บังคับ LINE_CHANNEL_SECRET (ห้ามข้ามการตรวจ signature)
 *  - production ห้ามเปิด dev routes
 *  - token / key อยู่ที่นี่ที่เดียว ห้ามส่งเข้า prompt
 *
 * ย้ายมาจาก MVP: line_/config.js
 * สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
 */
