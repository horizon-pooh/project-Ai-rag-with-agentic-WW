# src/ai/

ส่วนที่คุยกับ Softnix: ส่งคำถาม รับ markdown แปลงเป็นสินค้า

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `aiClient.js` | ประกอบ prompt + history แล้วสรุปผลเป็น ตอบได้/ตอบไม่ได้ + รายการสินค้า | `line_/aiClient.js` |
| `llmClient.js` | ยิง POST /chat/completions ไป Softnix (มี timeout, ไม่ throw) | `line_/llmClient.js` |
| `softnixParser.js` | แปลง markdown ที่ Softnix ตอบ เป็นรายการสินค้า | `line_/softnixClient.js` |

## ไฟล์อื่น

- `prompts/` — system prompt แยกเป็นไฟล์ข้อความ แก้ได้โดยไม่ต้องแตะโค้ด
