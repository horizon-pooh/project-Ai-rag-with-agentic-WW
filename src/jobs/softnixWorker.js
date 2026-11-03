/**
 * src/jobs/softnixWorker.js
 *
 * ดึงงานจากคิวทีละงาน (เท่าที่ Softnix รับไหว) แล้วส่งผลให้ sendGuard
 *
 * รายละเอียด:
 *  - SOFTNIX_CONCURRENCY ตอนนี้ = 1 (วัดจริง: Softnix ทำทีละคิว)
 *  - เกิน SOFTNIX_HARD_LIMIT_MS → เลิกรอ ส่งต่อคน
 *  - ต่ออายุ loading animation ระหว่างรอ
 *
 * ย้ายมาจาก MVP: line_/server.js runAiTurn() + lineClient startLoadingKeepAlive
 * สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
 */
