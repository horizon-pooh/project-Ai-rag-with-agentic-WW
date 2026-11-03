# src/pipeline/

ด่านก่อนถึงการตัดสินใจ: ตัดของซ้ำ, เรียงคิวต่อคน, ตอบ FAQ

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `dedupe.js` | ตัด event ซ้ำที่ LINE ส่งมาอีกรอบ (webhookEventId) เก็บใน Redis | `line_/dedupe.js` |
| `userQueue.js` | ข้อความของลูกค้าคนเดียวกันทำทีละข้อความ (lock ต่อ userId ใน Redis) | `line_/userQueue.js` |
| `filter.js` | ตอบ FAQ/คำทักทายโดยไม่ใช้ AI และปล่อยผ่าน keyword ที่ Vendor 1 ตอบเอง | `line_/filter.js` |
