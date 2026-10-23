# CLAUDE.md

Stack (ยังเป็นข้อเสนอ): Python 3.12, FastAPI, procrastinate (คิวบน Postgres), Postgres, MinIO, Docker Compose
สถานะ: มีโค้ดรอบแรก (ยังไม่ทดสอบกับ Postgres/LINE/AI จริง) — ดู docs/PLAN.md

## คำสั่ง (เมื่อมีโค้ด)
- `uv sync` — ติดตั้ง dependency
- `uv run pytest` — รันเทสต์
- `uv run ruff check .` — lint
- `docker compose up` — รันทั้งระบบ

## กติกา
- `docs/Line_ai_bot.md` คือแบบหลัก; เอกสารอื่นใน `docs/` และ `README.md` เป็นแบบเก่าที่ติดป้ายไว้
- `src/`, `dev/`, `test/`, `deploy/ecosystem.config.js` = แบบเก่า (Node) ห้ามแก้ ห้ามลบ ห้ามย้าย
  เทสต์ของแบบใหม่อยู่ที่ `tests/` (มี s)
- มีแต่ `app/repositories/` ที่เขียน SQL
- `app/pipeline/` ไม่เรียก LINE และไม่เขียน SQL เอง (เรียกผ่าน services / repositories)
- ข้อความขาออกทุกข้อความผ่าน `app/pipeline/steps/send.py` (sendGuard) เท่านั้น
- ส่งให้ AI API เฉพาะข้อความที่ redact แล้ว (`app/privacy/redact.py`); secret อยู่ใน `.env` เท่านั้น
- รายการคำภาษาไทยใน `prompts/rules/` ทุกคำต้องมีเคสทดสอบ
- เรื่องที่ขึ้นกับ AI API ที่ยังไม่รู้ ให้ใส่ป้าย "รอคำตอบ" อย่าเดา

## อ่านต่อ
- `docs/Line_ai_bot.md` — สถาปัตยกรรม, flow, หลักการเขียนโปรแกรม, Stack
- `docs/PLAN.md` — แผนงาน, สัญญาระหว่างโมดูล, เรื่องค้าง
- `filemap.md` — ไฟล์ไหนทำอะไร
- `docs/decisions.md` — การตัดสินใจและเหตุผล
