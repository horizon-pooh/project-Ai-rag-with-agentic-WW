# src/conversation/

หัวใจของระบบ: state ของลูกค้าแต่ละคน และการตัดสินว่าข้อความไปทางไหน

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `stateMachine.js` | กฎการเปลี่ยน state ของลูกค้าแต่ละคน รวมไว้ที่เดียว | `line_/server.js` |
| `handleMessage.js` | ตัดสินว่าข้อความนี้ไปทางไหน: เงียบ / filter / ส่งต่อคน / เข้าคิว Softnix | `line_/server.js` |
| `handlePostback.js` | จัดการปุ่มที่ลูกค้ากด เช่น ดูรายละเอียดสินค้า | `line_/server.js` |
| `escalate.js` | ส่งต่อคน: ตั้ง ESCALATED, สร้าง closeToken, ยกเลิกงาน Softnix ที่ค้าง, แจ้ง call center | `line_/server.js` |
