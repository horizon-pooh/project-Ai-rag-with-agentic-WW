# src/line/

ส่วนที่คุยกับ LINE ทุกข้อความขาออกต้องผ่าน sendGuard.js

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `lineApi.js` | เรียก LINE Messaging API: reply / push / loading (ไฟล์เดียวที่ถือ channel token) | `line_/lineClient.js` |
| `sendGuard.js` | ประตูเดียวของทุกข้อความขาออก: เช็ค state ล่าสุดก่อนส่งทุกครั้ง | ใหม่ |
