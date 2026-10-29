# API ค้นหาสินค้า

ใช้ Fastify และ PostgreSQL รับ JSON รุ่น `1` จากระบบสนทนา รองรับชื่อและรหัสหมวด สเปก และฟีเจอร์

## เปิดใช้

ใช้ `.env` และ DB tunnel เดิม ไม่คัดลอกไฟล์ตัวอย่างทับค่าที่ตั้งไว้

```bash
npm run api:configure
npm run dev
npm run api:check
```

`api:configure` สร้างหรือเก็บ token เดิมโดยไม่พิมพ์ค่า และต้องใช้ `DATA_SOURCE=postgres` หากเปิดหมวดแบบเวกเตอร์ใช้ `npm run dev:semantic` ส่วนการรวมอันดับสินค้าใช้ `npm run dev:hybrid` ตาม [คู่มือ RRF](product-hybrid-search.md)

## เส้นทาง

| เส้นทาง | การใช้งาน |
|---|---|
| `GET /health` | ตรวจว่าโปรแกรมทำงาน ไม่ได้ยืนยันว่าฐานพร้อม |
| `POST /api/search` | ค้นสินค้า |
| `GET /api/products/:article_id` | อ่านรายละเอียดรายชิ้น |

เส้นทางสินค้าใช้ `Authorization: Bearer <token>` และ JSON ใช้ `Content-Type: application/json` ตรวจ token ก่อนอ่านเนื้อหา รับ body สูงสุด 64 KiB

```json
{
  "version": "1",
  "query": "ทีวี",
  "semantic_query": "ทีวีสำหรับดูหนัง",
  "filters": {
    "category_query": "ทีวี OLED",
    "price": {"max": 25000, "currency": "THB"},
    "specs": [{"attribute_query": "ขนาดหน้าจอ", "op": "eq", "value": 55, "unit": "นิ้ว"}]
  },
  "page": 1,
  "limit": 5,
  "sort": "relevance"
}
```

ถ้าไม่ส่งหมวด ระบบอาจใช้ `query` เป็นชื่อหมวด คำกำกวมให้เลือกเพิ่ม คำขอที่มีแต่ Article/รุ่นโดยไม่มีหมวดยังต้องระบุขอบเขต หรือใช้ GET รายละเอียดสำหรับ Article เดียว

## สถานะ HTTP

| HTTP | ความหมาย |
|---|---|
| 200 | ค้นสำเร็จ ไม่มีผล หรือให้เลือกเงื่อนไขเพิ่ม ดู `status` ใน JSON |
| 400 | JSON หรือค่าที่ส่งไม่ถูกต้อง |
| 401 | token ไม่ถูกต้อง |
| 404 | ไม่พบสินค้าหรือไม่มีสิทธิ์เห็น รวมถึง draft |
| 409 | เงื่อนไขขัดกัน |
| 413 / 415 | body ใหญ่เกิน / ชนิดเนื้อหาไม่รองรับ |
| 503 | ฐานหรือข้อมูลผลลัพธ์ไม่พร้อม ไม่ใช่ไม่มีสินค้า |

ผู้เรียกต้องอ่านทั้ง HTTP และ `status` คงข้อความข้อมูลจำลอง เงื่อนไขที่ใช้ไม่ได้ และเหตุผลของ alternatives ไว้เมื่อนำไปตอบลูกค้า

เมื่อเปิด product hybrid และรวมอันดับสำเร็จจะคืน `semantic_available: true` หากเวกเตอร์หรือโมเดลไม่พร้อมใช้ keyword และคืน false พร้อมเหตุผล รหัสคำขอสร้างจากเซิร์ฟเวอร์และข้อผิดพลาดไม่ส่ง SQL หรือรหัสผ่านกลับมา

การใช้งานบนเซิร์ฟเวอร์ดู [คู่มือติดตั้ง](server-deployment.md) และ [คู่มือผู้เรียก](integration-handoff.md) OpenAPI ใช้ schema จากตัวตรวจจริง สร้างใหม่ด้วย npm run api:export เมื่อเปลี่ยนสัญญา
