# src/routes/

ทางเข้า HTTP บางที่สุด แค่รับแล้วส่งต่อ

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `webhook.js` | POST /webhook: ตอบ 200 ทันที แล้วส่ง event เข้า pipeline | `line_/server.js:90` |
| `close.js` | GET /close?token=: เจ้าหน้าที่กดปิดงาน (token ใช้ได้ครั้งเดียว อายุ 24 ชม.) | `line_/server.js` |
| `health.js` | GET /health: ให้ pm2/monitor เช็คว่า server, Redis และคิว Softnix ยังปกติ | ใหม่ |
