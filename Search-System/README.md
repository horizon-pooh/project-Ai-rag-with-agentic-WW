# ระบบค้นหาสินค้า

รับ JSON จากระบบสนทนา แล้วใช้ PostgreSQL เลือกหมวด ตรวจตัวกรอง และค้นสินค้า ใช้ Node.js, TypeScript, Fastify และ `pg`

ลำดับการทำงาน: ตรวจคำขอ → หาหมวด → ตรวจสเปก/แบรนด์/ฟีเจอร์ → ค้นและจัดอันดับใน SQL → คืนข้อมูลสินค้า

ระบบอ่านเฉพาะสินค้าที่เผยแพร่ ผลตรงเงื่อนไขมาก่อนผลใกล้เคียง พร้อมบอกเงื่อนไขที่ไม่ตรง ข้อมูลจำลองยังมี `is_mock` และ `mock_notice`

จัดอันดับได้ทั้ง keyword และ hybrid แบบ RRF เมื่อเปิดใช้และเวกเตอร์ในขอบเขตครบ รวมอันดับทั้งผลตรงและ alternatives โดยให้ผลตรงมาก่อนและระบุเงื่อนไขที่ไม่ตรง

## เริ่มใช้งาน

รันคำสั่งในโฟลเดอร์ `Search-System` ใช้ไฟล์ `.env` เดิม หากยังไม่มีให้คัดลอกจาก `.env.example` แล้วตั้งค่าฐานข้อมูลและรหัสเรียก API

```bash
npm ci
npm run db:check
npm run dev
```

พัฒนาบน Mac ต้องเปิด SSH tunnel ไปฐานก่อน รหัสผ่านและ token เก็บในไฟล์ `.env` ส่วนตัวนอก Git

## API

- `GET /health` ตรวจว่าโปรแกรมทำงาน
- `POST /api/search` ค้นสินค้าด้วย JSON
- `GET /api/products/:article_id` อ่านรายละเอียดสินค้า

สองเส้นทางสินค้าใช้ `Authorization: Bearer <token>` ดูรูปแบบใน [คู่มือ API](docs/tool-api.md)

## เวกเตอร์

เปิดโมเดล Qwen บนเซิร์ฟเวอร์และรอพร้อมก่อนรัน Node ส่วน embedding บน Mac ต้องเปิด tunnel ของโมเดลด้วย

- [เปิดโมเดล](docs/embedding-provider.md)
- [สร้างเวกเตอร์ทีละชุด](docs/embedding-backfill.md)
- [ใช้เวกเตอร์ช่วยหาหมวด](docs/category-semantic-resolution.md)
- [รวมอันดับสินค้าแบบ RRF](docs/product-hybrid-search.md)
- [แคชคำค้นและ fallback](docs/query-embedding-cache.md)
- [ประเมินผลค้นหา](docs/search-evaluation.md)
- [ผลประเมินชุดปัจจุบัน](docs/search-evaluation-results.md)
- [วัดความเร็วและตรวจสิทธิ์](docs/search-performance.md)
- [ผลวัดรอบปัจจุบัน](docs/search-performance-results.md)
- [ติดตั้งบนเซิร์ฟเวอร์](docs/server-deployment.md)
- [ส่งต่อให้ระบบสนทนา](docs/integration-handoff.md)

## ตรวจโค้ด

```bash
npm run typecheck
npm run build
npm test
```

การแก้ฐานใช้ migration ตามลำดับ ตรวจ `npm run db:migrate:plan` ก่อนเสมอ และไม่แก้ไฟล์ SQL ที่เคยรันแล้ว
