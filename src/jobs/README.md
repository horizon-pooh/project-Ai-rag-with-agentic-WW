# src/jobs/

งานที่ใช้เวลานาน (Softnix 100–230 วิ) แยกออกมาเป็นคิว ไม่ให้ค้างใน request

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `softnixQueue.js` | คิวงานไป Softnix เก็บใน Redis: จำกัดความยาว, ยกเลิกได้, ไม่หายตอน restart | ใหม่ |
| `softnixWorker.js` | ดึงงานจากคิวทีละงาน (เท่าที่ Softnix รับไหว) แล้วส่งผลให้ sendGuard | `line_/server.js` |
