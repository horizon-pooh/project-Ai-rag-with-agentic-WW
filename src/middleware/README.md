# src/middleware/

ด่านตรวจก่อนเข้า route

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `lineSignature.js` | ตรวจ x-line-signature (HMAC-SHA256 ของ raw body) ไม่ผ่านตอบ 401 | `line_/server.js` |
