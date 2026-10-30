/**
 * src/server.js
 *
 * จุดเริ่มโปรแกรม: โหลด config, ต่อ Redis, เปิด HTTP server, เริ่ม worker และปิดอย่างปลอดภัย
 *
 * รายละเอียด:
 *  - config ไม่ครบ → หยุดทันที ไม่เปิดรับ traffic
 *  - SIGTERM → หยุดรับ webhook ใหม่, รอ job ที่กำลังทำ, ปิด Redis
 *  - งานใน Redis ไม่หายตอน restart เพราะ worker กลับมาดึงต่อ
 *
 * ย้ายมาจาก MVP: line_/server.js (start, SIGINT/SIGTERM)
 * สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
 */
