/**
 * src/conversation/handleMessage.js
 *
 * ตัดสินว่าข้อความนี้ไปทางไหน: เงียบ / filter / ส่งต่อคน / เข้าคิว Softnix
 *
 * รายละเอียด:
 *  - ESCALATED → ไม่เรียก AI ไม่ตอบ (แค่บันทึก)
 *  - ข้อความใหม่ระหว่างรอ Softnix → งานเก่าถูกแทน (superseded)
 *
 * ย้ายมาจาก MVP: line_/server.js handleEvent()
 * สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
 */
