# deploy/

> **สถานะ (22 ต.ค. 2026): แบบเก่า — แบบหลักอยู่ที่ `docs/Line_ai_bot.md` (หัวข้อ Stack)**
> ยังใช้ได้: Redis bind 127.0.0.1, reverse proxy + HTTPS ให้ LINE ยิง webhook
> ล้าสมัย: pm2/`ecosystem.config.js` — แบบใหม่ใช้ Docker Compose (`docker-compose.yml` ที่ root เป็น stub)

ติดตั้งบน server บริษัท: pm2 + Redis + HTTPS

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `ecosystem.config.js` | config ของ pm2 (1 process, restart อัตโนมัติ, log rotate) | ใหม่ |

## ที่จะมีในโฟลเดอร์นี้

- `ecosystem.config.js` — pm2
- `redis.md` — ติดตั้ง Redis บนเครื่องเดียวกัน (bind 127.0.0.1, เปิด AOF)
- `https.md` — reverse proxy (nginx) + certificate ให้ LINE ยิง webhook ได้
