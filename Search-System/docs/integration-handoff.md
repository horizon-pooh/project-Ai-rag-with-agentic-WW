# ส่งต่อให้ระบบสนทนา

ระบบสนทนาส่งความต้องการเป็น JSON → API ตรวจคำขอ → SQL เลือกหมวดและตัวกรอง → ค้น published products และจัดอันดับ → คืนข้อเท็จจริงพร้อมเหตุผลให้ AI ตอบลูกค้า

ใช้ `POST /api/search` และ `GET /api/products/:article_id` ผ่าน Tailscale ที่ `http://100.116.20.59:3000` เมื่อผู้ดูแลติดตั้งแล้ว ผู้เรียกใช้ Bearer token ไม่ได้รับรหัสฐานข้อมูล URL นี้ยังไม่ถือว่าเปิดใช้งานจนทดสอบจากเครื่องผู้เรียกผ่าน

## รูปแบบคำขอ

ดู schema ที่ [OpenAPI](tool-api.openapi.json) ซึ่งสร้างจากตัวตรวจจริง ตัวอย่าง:

- [รู้รหัสหมวด สเปก และฟีเจอร์](examples/tool-search-v1/request-ids.json)
- [ส่งชื่อภาษาคน](examples/tool-search-v1/request-human.json)
- [metadata หนึ่งหมวดที่ตรวจจากฐาน](examples/tool-search-v1/metadata-ap0101.json)

ส่งรหัสหรือชื่อได้ หากส่งทั้งสองต้องตรงกัน รหัสจาก knowledge ยังตรวจซ้ำกับฐาน ไม่ข้ามสิทธิ์หรือเชื่อรหัสที่ AI เดาเอง สเปกในแต่ละหมวดอาจใช้คนละรหัส

OR ระหว่างหมวด/แบรนด์ AND ระหว่างสเปก/ฟีเจอร์ ใช้ `feature_ids_all` เมื่อต้องมีทุกฟีเจอร์ ราคาเป็น THB ตัวเลขและ boolean ต้องเป็นชนิดจริง ห้ามส่ง "9" แทน 9 หรือ "false" แทน false

`semantic_query` ช่วยเรียงสินค้า ไม่ใช่ตัวกรองบังคับ หากต้องการความจุขั้นต่ำต้องส่งใน specs ด้วย Article/รุ่นเจาะจงยังต้องระบุหมวดสำหรับ POST หรือใช้ GET รายละเอียดเมื่อรู้ Article

## อ่านผล

- HTTP 200 ต้องอ่าน status อีกครั้ง: ok, no_matches หรือ needs_clarification
- ผลตรงมาก่อน alternatives ที่มีเหตุผลบอกข้อไม่ตรง/ไม่ทราบ ไม่เรียก alternatives ว่าตรงทุกเงื่อนไข
- `matches_applied_filters` หมายถึงผ่านเฉพาะข้อที่ใช้ตรวจได้ ตรวจ unapplied_filters และ all_requested_filters_applied ร่วมด้วย
- ถ้าต้องถามเพิ่มให้ใช้ clarifications ไม่เลือกคำตอบแทนลูกค้าเอง
- คง is_mock/mock_notice ไว้ สินค้าชุดนี้เป็นข้อมูลจำลอง ไม่อ้างว่ารุ่นและคุณสมบัติเป็นสินค้าจริงของแบรนด์
- semantic_available=true หมายถึงใช้ product vector จัดอันดับจริง หาก false ให้อ่าน warnings ไม่อ้างว่าค้นความหมายแล้ว

401 ให้ตรวจ token, 409 เงื่อนไขขัดกัน, 404 ไม่พบ published product รวม draft, 503 ระบบไม่พร้อม ห้ามแปล 503 ว่าไม่มีสินค้า ห้ามนำ SQL error หรือรายละเอียด credentials ไปตอบลูกค้า

เก็บ request_id/x-request-id เพื่อแจ้งปัญหา โดยไม่ log token ข้อความลูกค้า หรือ response ทั้งก้อน ส่งเงื่อนไขที่ลูกค้าตกลงครบทุกครั้ง เพราะ API ไม่เก็บประวัติสนทนาให้

## ตรวจจากเครื่องผู้เรียก

คัดลอก .env.peer.example เป็น .env.peer.local แล้วใส่เฉพาะ caller token ผ่านช่องทางส่วนตัว ในสำเนาโปรเจกต์ที่ build แล้ว:

```bash
node --env-file=.env.peer.local dist/scripts/check-peer-api.js --url http://100.116.20.59:3000
```

ต้องได้ peer_integration_ready ตรวจค้น รายละเอียด draft identity lock และ JSON ผิดด้วย token ที่ถูกต้อง การผ่าน /health อย่างเดียวไม่พอ ผู้เรียกไม่ต้องตั้ง DATA_SOURCE หรือ SEARCH_DATABASE_PASSWORD

เครื่องมือแสดงเฉพาะผลตรวจและรหัส warning ไม่พิมพ์ token หากเรียกจากบริการที่ไม่ได้อยู่ใน Tailscale ต้องวางเครือข่ายร่วมกันก่อน ไม่เปิดพอร์ตฐานหรือ API สู่สาธารณะเพื่อแก้ปัญหาการเชื่อมต่อ
