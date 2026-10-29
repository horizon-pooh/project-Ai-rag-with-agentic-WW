# วัดความเร็วและตรวจสิทธิ์

เครื่องมือรอบนี้อ่านข้อมูลและสร้างรายงาน ไม่เพิ่มดัชนีหรือเปลี่ยนสินค้า ใช้บัญชี `search_api` ตามเดิม

## ตรวจ query plan

เปิด DB tunnel แล้ว:

```bash
mkdir -p reports
npm run db:inspect-search -- --out reports/search-plans.json
```

ตรวจ published/draft สิทธิ์เขียน ตาราง private และข้อมูลประกอบของ Article 106045 ที่กำหนดเป็น draft ใน baseline ถ้าสถานะรายการนี้เปลี่ยนให้ทบทวน probe ก่อนสรุปว่าเป็นปัญหาสิทธิ์

รัน `EXPLAIN ANALYZE BUFFERS` เฉพาะ SELECT ที่กำหนดไว้ 6 คำสั่ง จำกัดเวลาต่อคำสั่ง 3 วินาที ส่วน probe สิทธิ์เขียนใช้ EXPLAIN โดยไม่มี ANALYZE จึงไม่รัน UPDATE/DELETE แม้สิทธิ์ตั้งผิด

ทดสอบ statement timeout 25 ms กับ `pg_sleep(0.1)` ใน transaction อ่านของตัวตรวจ แล้ว rollback savepoint และยืนยันว่า connection ยังใช้ได้ ไม่เปลี่ยน timeout ของ API process อื่น

plan ของฟังก์ชัน PL/pgSQL แสดงเพียง wrapper ไม่ใช่แผนภายในทั้งหมด คำสั่งอ่านประกอบช่วยดูการใช้ดัชนี แต่ไม่แทน nested plan ของ search function อย่าเพิ่ม/ลบดัชนีจากชื่อ Seq Scan เพียงอย่างเดียว

## วัด API

เปิดโมเดลและรอ health ก่อน Node สำหรับ Mac ใช้ tunnel เดิม ไม่เปิดโมเดลซ้ำ ตัวอย่างเปิด keyword:

```bash
PORT=3000 SEARCH_CATEGORY_SEMANTIC_ENABLED=false SEARCH_PRODUCT_HYBRID_ENABLED=false npm run dev
```

อีก terminal:

```bash
npm run search:benchmark -- --workload docs/examples/search-benchmark.json --url http://127.0.0.1:3000 --out reports/keyword-c1.json --label keyword --iterations 40 --warmup 4 --concurrency 1 --seconds 120
```

เปลี่ยนเป็น concurrency 5 และชื่อรายงานใหม่เพื่อเทียบ จากนั้นปิด Node แล้วเปิด hybrid โดยปิดแคช:

```bash
SEARCH_CATEGORY_SEMANTIC_ENABLED=false SEARCH_QUERY_CACHE_ENABLED=false npm run dev:hybrid
```

วัดอีกครั้งด้วย label `hybrid-cache-off` แล้วเริ่ม Node ใหม่ด้วย `SEARCH_QUERY_CACHE_ENABLED=true` เพื่อวัด `hybrid-cache-on` ใช้ workload และจำนวนคำขอเท่ากัน label เป็นคำอธิบายที่ผู้รันระบุ ต้องอ่าน `semantic_used` และ `warning_counts` ร่วมด้วย ไม่รับประกันว่าทุกคำขอใช้เวกเตอร์

ชุด `search-benchmark-broad.json` ใช้หมวดแม่ AP0000 และหน้าลึก วัดแยกจากชุดหลัก ไม่ปนเปอร์เซ็นไทล์ของคนละ workload รายงานมี workload hash เพื่อช่วยเทียบชุดเดียวกัน

## อ่านรายงาน

- แยก warmup ออกจากรอบวัด p50/p95 ใช้ nearest-rank มีจำนวน sample กำกับ
- `latency_all` รวมคำขอผิดพลาด `latency_success` เฉพาะคำตอบที่ผ่านสัญญาและสถานะที่คาดไว้ อย่าอ่านเฉพาะตัวหลังแล้วมองข้าม failed
- throughput นับคำขอที่จบต่อวินาที เป็นการยิงแบบรอผลก่อนเติมงาน ไม่ใช่ขีดจำกัดผู้ใช้สูงสุดของระบบ
- แยกตาม probe เพราะรายละเอียดสินค้าและการขอคำชี้แจงอาจเร็วกว่าการค้นมาก จำนวน sample น้อยยังใช้รับรอง SLA ไม่ได้
- หากโมเดลหรือข้อมูลเวกเตอร์ไม่พร้อม keyword fallback ยังเป็นคำตอบที่ถูกต้อง แต่ต้องแยกจากการวัด hybrid จริง

คำสั่งใช้ loopback เท่านั้น ไม่ตาม redirect เก็บเฉพาะเวลา สถานะ และรหัส warning ไม่เก็บผลสินค้า เวกเตอร์ token หรือ error ดิบ ไม่ทับรายงานเดิม จึงเปลี่ยนชื่อไฟล์เมื่อรันซ้ำ

จำกัด 300 รอบวัด warmup 20 และงานพร้อมกัน 10 ต่อรอบ มี deadline รวมสูงสุด 5 นาทีและ timeout คำขอ 20 วินาที timeout จะหยุดเติมงานใหม่และยกเลิก transport ไม่สร้างคิวไม่จำกัด

ก่อนเทียบให้หยุด import/backfill และบันทึกโมเดล จำนวนข้อมูล เครื่องที่รัน และสถานะแคช อย่าสรุปผล Mac ผ่าน tunnel เป็นความเร็วของ API บนเซิร์ฟเวอร์ ให้รันซ้ำหลังติดตั้งจริงใน Commit 27

## สิ่งที่คงเดิม

pool สูงสุด 5 ต่อ process, SQL timeout 10 วินาที, query timeout 15 วินาที และแคชคำค้นเดิม ไม่เปิดตารางเวกเตอร์หรือเพิ่ม endpoint วินิจฉัยสาธารณะ ไม่มี migration ใน commit นี้

native tests ใช้ PostgreSQL แยกเพื่อตรวจ timeout จริง PGlite ตรวจพฤติกรรมนี้แทน native ไม่ได้ ดู [ฐานทดสอบแยก](native-embedding-tests.md)
