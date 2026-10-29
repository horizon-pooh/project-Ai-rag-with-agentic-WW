# ทดสอบกับ PostgreSQL แยก

`npm test` ใช้ PGlite เป็นหลัก การทดสอบ native ใช้ PostgreSQL 18.6 พร้อม pgvector 0.8.2, pgcrypto และ pg_trgm ใน cluster ทดสอบใหม่เท่านั้น มีการสร้าง role และแก้ตาราง fixture จึงไม่ใช้ฐานสินค้าจริง

ผู้ดูแลทดสอบชื่อ `catalog_test_admin` ฐานแต่ละชุดแยกดังนี้:

| ชุดทดสอบ | ฐาน |
|---|---|
| ตารางและสิทธิ์เวกเตอร์ | `catalog_embedding_test_c19` |
| สร้างเวกเตอร์เป็นชุด | `catalog_embedding_test_c20` |
| หาหมวดด้วยเวกเตอร์ | `catalog_category_test_c21` |
| รวมอันดับสินค้า | `catalog_product_test_c22` |
| จัดอันดับ alternatives | `catalog_alternatives_test_c23` |
| แคชและข้อมูลล่าสุด | `catalog_cache_test_c24` |

ตัวอย่างชุดตาราง ใช้ socket ส่วนตัวใน `/private/tmp` และพอร์ตของ cluster ทดสอบ:

```bash
EMBEDDING_STORAGE_TEST_HOST=/private/tmp/your-test-socket EMBEDDING_STORAGE_TEST_PORT=55439 npm run test:embedding-storage:native
```

ชุด backfill ใช้ `EMBEDDING_BACKFILL_TEST_MODE=native` กับตัวแปร host/port เดียวกัน แล้วรัน `tests/embedding-backfill.test.ts` ผ่าน `node --import tsx --test`

ชุดหมวดใช้ `CATEGORY_SEMANTIC_TEST_MODE=native`, `CATEGORY_SEMANTIC_TEST_HOST` และ `CATEGORY_SEMANTIC_TEST_PORT` แล้วรัน `tests/category-semantic.test.ts`

เริ่มจาก cluster/ฐานและ role ทดสอบใหม่ก่อนแต่ละรอบ รหัสผ่านทดสอบถ้ามีเก็บใน environment ไม่เข้า Git ปิด cluster เมื่อจบ การผ่าน fixture ไม่ได้ยืนยันคุณภาพผลค้นหรือความเร็วของข้อมูลจริง

ชุด alternatives ใช้ `HYBRID_ALTERNATIVES_TEST_MODE=native`, `HYBRID_ALTERNATIVES_TEST_HOST` และ `HYBRID_ALTERNATIVES_TEST_PORT` แล้วรัน `tests/hybrid-alternatives.test.ts` ผ่าน `node --import tsx --test` ใน cluster ใหม่เท่านั้น

ชุดแคชใช้ `QUERY_CACHE_TEST_MODE=native`, `QUERY_CACHE_TEST_HOST` และ `QUERY_CACHE_TEST_PORT` แล้วรัน `tests/query-cache-integration.test.ts` ใน cluster ใหม่ผ่าน socket ส่วนตัว บัญชีและฐานต้องตรงกับชื่อข้างต้น
