# การเชื่อมต่อฐานข้อมูล

ใช้ `supabase_db_product` บน BPLUTONIX เป็น PostgreSQL 18.6 ฐานนี้มี schema และข้อมูลเดิมอยู่แล้ว จึงไม่ต้องสร้างหรือนำเข้าซ้ำ

พัฒนาบน Mac ผ่าน SSH tunnel พอร์ต `15432` ไป `127.0.0.1:5432` บนเซิร์ฟเวอร์ หากรัน API บนเซิร์ฟเวอร์ให้เชื่อมผ่าน loopback โดยตรง

## บัญชีที่ใช้

- `search_api` อยู่ในกลุ่ม `catalog_search_reader` อ่านสินค้าที่เผยแพร่ ไม่มีสิทธิ์แก้ข้อมูล
- `embedding_worker` ใช้สร้างเวกเตอร์ตามสิทธิ์ที่กำหนด
- `antonius` เป็นเจ้าของตาราง ใช้รัน migration ผ่าน `.env.migration.local` และไม่มีสิทธิ์สร้าง role
- งานสร้าง role ใช้ผู้ดูแล PostgreSQL แยกจากบัญชีข้างต้น

RLS จำกัดสินค้าและข้อมูลประกอบให้เห็นเฉพาะ `published` สินค้า draft ไม่ถูกเปิดเพื่อให้การทดสอบผ่าน

## Migration

เก็บประวัติและ SHA256 ใน schema `search_migrations` ตัวตรวจ checksum เป็นส่วนที่โปรเจกต์เพิ่มจาก `node-pg-migrate`

```bash
npm run db:migrate:plan
npm run db:migrate:up
npm run db:check
npm run db:check-access
```

สำรองฐานและทดลองในฐานแยกก่อนติดตั้งจริง ใช้คำสั่งของโปรเจกต์เพื่อให้ตรวจ checksum ครบ
ไม่แก้ไฟล์ที่ถูกบันทึก checksum แล้ว ไม่ลบประวัติเพื่อบังคับรันซ้ำ หากต้องแก้ schema ให้เพิ่ม migration ใหม่

การย้อน migration ต้องตรวจสิทธิ์และข้อมูลที่จะได้รับผลกระทบก่อน เพราะบางขั้นถอนสิทธิ์ API หรือป้องกันการทิ้งประวัติงานเวกเตอร์
