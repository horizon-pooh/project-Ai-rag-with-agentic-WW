/**
 * src/conversation/stateMachine.js
 *
 * กฎการเปลี่ยน state ของลูกค้าแต่ละคน รวมไว้ที่เดียว
 *
 * รายละเอียด:
 *  - NEW → AI_HANDLING → ESCALATED → CLOSED → (ข้อความใหม่) NEW
 *  - ทุกที่ที่เปลี่ยน state ต้องเรียกผ่านไฟล์นี้
 *
 * ย้ายมาจาก MVP: line_/server.js + sessionStore.js (ส่วนเปลี่ยน state)
 * สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
 */
