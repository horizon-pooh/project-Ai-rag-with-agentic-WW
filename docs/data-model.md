# รูปแบบการเก็บข้อมูล

> **สถานะ (22 ต.ค. 2026): แบบเก่า — แบบหลักอยู่ที่ `docs/Line_ai_bot.md` (หัวข้อ ฐานข้อมูล)**
> ยังใช้ได้: ประมาณขนาด log, หมายเหตุค่าใช้จ่าย (โควตา push), redact log
> ล้าสมัย: เก็บทุกอย่างใน Redis — แทนที่โดย Postgres; Redis เหลือแค่คิว; key/TTL/session ด้านล่างไม่ใช้แล้ว

> ยังเป็นข้อเสนอ (ยังไม่มีโค้ด) — ยึดจาก `line_/sessionStore.js` ที่ใช้งานอยู่ แล้วย้ายจาก Map ในหน่วยความจำไปเก็บใน Redis

## หลักการ

1. **เก็บทุกอย่างใน Redis ที่เดียว** ไม่ใช้ฐานข้อมูล SQL ในเวอร์ชันแรก — ข้อมูลทั้งหมดเป็นข้อมูลชั่วคราวของบทสนทนา
2. **ทุก key มีวันหมดอายุ (TTL)** ข้อมูลลบตัวเองอัตโนมัติ — ดีต่อ PDPA และ Redis ไม่โตไม่หยุด
3. **ไม่เก็บ LINE token ใน Redis** token อยู่ใน `.env` เท่านั้น
4. **เปิด AOF** (`appendonly yes`, `appendfsync everysec`) — เครื่องดับเสียข้อมูลไม่เกิน ~1 วินาที

## key ทั้งหมด

| key | ชนิด | เก็บอะไร | หมดอายุ |
|---|---|---|---|
| `session:{userId}` | STRING (JSON) | state + ประวัติแชทของลูกค้า 1 คน | 24 ชม. หลังข้อความล่าสุด |
| `closetoken:{token}` | STRING → userId | ลิงก์ปิดงานของเจ้าหน้าที่ | 24 ชม. (ใช้แล้วลบทันทีด้วย `GETDEL`) |
| `escalated` | ZSET (score = escalatedAt) | รายชื่อลูกค้าที่อยู่กับเจ้าหน้าที่ — ให้ sweep หาคนที่เงียบเกิน 3 ชม. ได้โดยไม่ต้องไล่ทุก session | ลบเมื่อปิดงาน |
| `dedupe:{webhookEventId}` | STRING | กัน event ที่ LINE ส่งซ้ำ (`SET NX`) | 10 นาที |
| `lock:{userId}` | STRING | ข้อความของลูกค้าคนเดียวกันทำทีละข้อความ (`SET NX PX`) | 30 วินาที (กันค้างถ้า process ตาย) |
| `softnix:queue` | LIST ของ jobId | คิวรอส่ง Softnix เรียงตามมาก่อน | — |
| `softnix:processing` | LIST ของ jobId | งานที่ Softnix กำลังทำ (ย้ายด้วย `LMOVE`) — restart แล้วรู้ว่างานไหนค้าง | — |
| `softnix:job:{jobId}` | STRING (JSON) | รายละเอียดงาน 1 งาน | 1 ชม. |
| `ops:killswitch` | STRING | มี key นี้ = เปิดทางหลบ | ไม่หมดอายุ (ปิดเอง) |
| `stats:{YYYY-MM-DD}` | HASH (ตัวนับ) | ตัวเลขรายวันให้หัวหน้า | 90 วัน |

## หน้าตาข้อมูล

### `session:{userId}`

เหมือน MVP แต่ตัด field ของฟีเจอร์ที่ตัดออก (แบบประเมิน, clarifier) และเพิ่ม `activeJobId`

```json
{
  "userId": "Ucbe3847...",
  "state": "AI_HANDLING",
  "createdAt": 1792564906192,
  "updatedAt": 1792565031909,
  "escalatedAt": null,
  "closeToken": null,
  "escalationNoticeSent": false,
  "activeJobId": "job_8f2c",
  "history": [
    { "role": "user", "text": "มีตู้เย็น 2 ประตูไหม", "timestamp": 1792564906199 },
    { "role": "ai",   "text": "ขอแนะนำ ...",          "timestamp": 1792564923800 }
  ]
}
```

- `state`: `NEW` | `AI_HANDLING` | `ESCALATED` | `CLOSED`
- `activeJobId`: งาน Softnix ที่ลูกค้ารออยู่ — **sendGuard ส่งคำตอบได้เฉพาะงานที่ตรงกับค่านี้** ลูกค้าถามเรื่องใหม่ = ค่านี้เปลี่ยน = คำตอบเก่าถูกทิ้ง
- `history`: เก็บไม่เกิน 50 ข้อความ (เท่า MVP) ส่งให้ Softnix แค่ 12 ข้อความล่าสุด

### `softnix:job:{jobId}`

```json
{
  "jobId": "job_8f2c",
  "userId": "Ucbe3847...",
  "text": "มีตู้เย็น 2 ประตูไหม",
  "status": "queued",
  "createdAt": 1792564906300,
  "startedAt": null
}
```

- `status`: `queued` → `running` → `done` | `cancelled` (ส่งต่อคน) | `superseded` (ถามเรื่องใหม่) | `timeout` (เกิน 5 นาที)
- ไม่ copy history มาเก็บซ้ำ — worker อ่านจาก `session:{userId}` ตอนเริ่มงาน

### `stats:{YYYY-MM-DD}`

```
filter_answered   812     ← ตอบจาก FAQ โดยไม่ใช้ AI
ai_answered       201
escalated          64
timeout            12     ← Softnix ตอบไม่ทัน 5 นาที
queue_full          9     ← คิวเต็ม เสนอเจ้าหน้าที่แทน
dropped_late        5     ← sendGuard ทิ้งคำตอบที่มาช้า
wait_ms_sum  30150000     ← หารด้วย ai_answered = เวลารอเฉลี่ย
```

## เส้นทางของข้อมูล 1 ข้อความ

1. webhook เข้า → `SET dedupe:{eventId} NX` (ซ้ำ = ทิ้ง)
2. `SET lock:{userId} NX` → อ่าน `session:{userId}`
3. คำถามสินค้า → สร้าง `softnix:job:{jobId}`, ตั้ง `activeJobId`, `RPUSH softnix:queue` (ถ้า `LLEN` เกิน 5 → ไม่รับ เสนอเจ้าหน้าที่)
4. worker `LMOVE softnix:queue → softnix:processing` → เรียก Softnix
5. ได้คำตอบ → sendGuard อ่าน `session:{userId}` อีกรอบ: state ยังไม่ใช่ `ESCALATED` **และ** `activeJobId` ยังตรง → ส่ง / ไม่ตรง → ทิ้ง + `HINCRBY stats dropped_late`
6. ลบงานออกจาก `softnix:processing`

## ขนาดโดยประมาณ

ลูกค้าไม่เกิน ~2,000 คน/วัน × session ละไม่เกิน ~10 KB (history 50 ข้อความ) ≈ **20 MB** ต่อวัน และลบตัวเองเมื่อครบ 24 ชม. — Redis บนเครื่องเดียวกันรับสบาย

## ค่าใช้จ่ายในการเก็บ

- **Redis:** ติดตั้งบน server บริษัทเอง ไม่มีค่าบริการรายเดือน ใช้แค่ RAM ของเครื่อง ข้อมูลไม่สะสมเพราะทุก key หมดอายุ — ใช้คงที่ราว 20–40 MB ไม่โตตามเวลา
- **log ไฟล์ คือส่วนที่โตจริง:** วัดจาก `line_/logs` ตอนนี้ MVP เขียน log ระดับ DEBUG (request/response ของ Softnix เต็มก้อน) **ประมาณ 35 KB ต่อข้อความ** × 4,500 ข้อความ/วัน ≈ 150 MB/วัน ≈ 4.5 GB/เดือน ถ้าไม่ลบจะโตไปเรื่อยๆ
  - production: ใช้ระดับ INFO (ไม่เขียน body เต็ม), ผ่าน `redact.js`, หมุนไฟล์ทุกวันและเก็บแค่ 30 วัน (ตั้งใน `deploy/ecosystem.config.js`)
- **ค่าใช้จ่ายที่บานได้จริงไม่ใช่การเก็บข้อมูล** แต่เป็นโควตา push message ของ LINE และค่า Softnix (`open-questions.md` ข้อ 6)

## เรื่องที่ยังไม่ได้ตัดสิน

- **ต้องเก็บข้อมูลระยะยาวไหม** (เช่น รายงานรายเดือน หรือข้อความลูกค้าเพื่อวิเคราะห์) ถ้าต้องการ ค่อยส่ง `stats:*` ออกไปเก็บใน SQL Server ทีหลัง — ต้องคุยเรื่อง PDPA ก่อน
- **log ไฟล์** เก็บกี่วัน (ตอนนี้ MVP เก็บข้อความลูกค้าเต็มๆ — production ต้องผ่าน `redact.js` ก่อน)
