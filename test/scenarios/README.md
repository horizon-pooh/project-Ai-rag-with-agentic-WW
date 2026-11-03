# test/scenarios/

เทสต์ตามเรื่องราวลูกค้าจริง

## ย้ายจาก MVP

- `line_/test/scenarios/noAiDuringHuman.test.js`, `conversations.test.js`

## ของใหม่

- `escalationRace.test.js` — ถามสินค้า → ขอคุยคนระหว่างรอ → คำตอบ Softnix มาช้า → ต้องถูกทิ้ง
- `restartMidJob.test.js` — restart ระหว่าง Softnix ทำงาน งานต้องไม่หาย
