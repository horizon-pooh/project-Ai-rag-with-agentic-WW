# deploy/

ติดตั้งบน server บริษัท: pm2 + Redis + HTTPS

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `ecosystem.config.js` | config ของ pm2 (1 process, restart อัตโนมัติ, log rotate) | ใหม่ |

## ที่จะมีในโฟลเดอร์นี้

- `ecosystem.config.js` — pm2
- `redis.md` — ติดตั้ง Redis บนเครื่องเดียวกัน (bind 127.0.0.1, เปิด AOF)
- `https.md` — reverse proxy (nginx) + certificate ให้ LINE ยิง webhook ได้
