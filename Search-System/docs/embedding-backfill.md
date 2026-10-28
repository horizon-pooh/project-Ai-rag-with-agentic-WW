# สร้างเวกเตอร์ทีละชุด

เป็นคำสั่งสำหรับผู้ดูแล ไม่ใช่งานที่เปิดอัตโนมัติพร้อม API ใช้เฉพาะหมวดและสินค้าที่เผยแพร่ ไม่เปลี่ยนสินค้า draft หรือตัดข้อความให้สั้นเอง

## ก่อนเริ่ม

เปิดโมเดล รอ health และเปิด DB/model tunnel ให้พร้อมตาม [คู่มือโมเดล](embedding-provider.md) ตรวจ SHA256 ของ GGUF จริง:

```bash
ssh -t antonius@100.116.20.59 'sha256sum ~/ai/models/Qwen3-Embedding-0.6B-Q8_0.gguf'
```

ใส่ค่า 64 ตัวแรกใน `EMBEDDING_ARTIFACT_SHA256` ของ `.env.embedding.local` ไฟล์ที่ตรวจต้องเป็นไฟล์เดียวกับที่โมเดลโหลด HTTP ไม่ได้รับรอง checksum ของไฟล์ให้เอง

## ลงทะเบียนและทดลอง

ลงทะเบียนด้วยบัญชี owner เฉพาะขั้นนี้ เลือกรหัสที่ว่างและตรง configuration เดิมหากรันซ้ำ:

```bash
npm run embedding:register -- --model-id EM0001 --activate
npm run embedding:backfill -- --kind category --model-id EM0001 --limit 20
npm run embedding:backfill -- --kind product --model-id EM0001 --limit 20
```

ไม่มี `--write` คือ dry-run ใช้ tokenizer จริงแต่ไม่สร้างเวกเตอร์หรือเขียนงาน การลงทะเบียนไม่แก้โมเดลอื่นและไม่สร้างเวกเตอร์

เริ่มเขียนจริงจำนวนเล็กก่อน:

```bash
npm run embedding:backfill -- --kind product --model-id EM0001 --limit 3 --max-tokens 10000 --max-seconds 120 --write
```

เปลี่ยน `--kind` เป็น `category` สำหรับหมวด ใช้บัญชี worker จาก `.env.embedding-worker.local` ไม่ใช้ owner สร้างเวกเตอร์

## ขอบเขตและการทำต่อ

- `--limit` เริ่มต้น 20 สำหรับ dry-run และ 3 สำหรับ write; write สูงสุด 1,000 ต่อคำสั่ง
- `--max-tokens` จำกัด token ของข้อความที่ผ่านตรวจต่อรอบ `--max-seconds` จำกัดเวลารอบ สูงสุด 3,600 วินาที
- `--after` ใช้ `next_after` จากรายงานเพื่อต่อจากตำแหน่งเดิม เมื่อตรวจซ้ำงานเก่าหรือ retry ให้เริ่มโดยไม่ใส่ cursor
- ข้ามงานที่ ready และยังตรงต้นทาง งานแต่ละชิ้นมี lease 120 วินาทีและบันทึกแบบตรวจรหัส lease/hash/template ซ้ำก่อนเขียน
- ไม่มี transaction ฐานค้างระหว่างเรียกโมเดล งานล้มเหลวชั่วคราว retry ได้สูงสุด 3 ครั้งต่อข้อความ/โมเดล รอ 30 และ 60 วินาที ไม่ retry ซ้อนใน provider

ดู `coverage_after.fresh/total`, `failed`, `lost_lease`, `issues` และ `stopped` หลังจบ `document_limit` คือครบจำนวนที่สั่ง ไม่ใช่ผิดพลาด ส่วน `source_exhausted` ไม่ได้รับรองว่าทุกงาน fresh

ข้อความเกิน context หรือขนาด byte จะถูกข้ามและรายงาน ไม่แก้ต้นทางเอง รายงานปัญหาเก็บสูงสุด 200 รายการ จึงต้องดู `issues_truncated` ด้วย ตัวเลข `embedding_tokens` นับเฉพาะผลที่สำเร็จ ไม่ใช่การใช้ทรัพยากรทั้งหมดเมื่อมีงานขาดการเชื่อมต่อ

ไม่ล้าง lease/จำนวน retry ขณะ worker ทำงาน และไม่ย้อน migration `0007` เพื่อทิ้งประวัติงาน หาก SQL บันทึกผลไม่แน่นอนให้หยุดตรวจ coverage ก่อนทำต่อ
