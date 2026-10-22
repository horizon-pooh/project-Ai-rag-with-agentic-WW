# filemap

## ราก
- `CLAUDE.md` — กติกาสำหรับ agent / อ่านทุกครั้ง
- `README.md` — แบบเก่า (ติดป้ายแล้ว) / ไม่ต้องแตะ
- `.env.example` — รายชื่อ env ที่ต้องตั้ง / แตะเมื่อเพิ่ม config
- `pyproject.toml` — dependency และเครื่องมือ Python / แตะเมื่อเพิ่ม package
- `docker-compose.yml` — โครง service ตาม Stack (ยังไม่ทดสอบ) / แตะเมื่อเพิ่ม service
- `Caddyfile` — HTTPS หน้า api เปิดเฉพาะ /webhook /health /close (โดเมนรอคำตอบ) / แตะเมื่อเพิ่ม route สาธารณะ

## docs/
- `docs/Line_ai_bot.md` — แบบหลัก: สถาปัตยกรรม, flow, หลักการ, Stack / อ่านก่อนทำงานทุกครั้ง
- `docs/decisions.md` — บันทึกการตัดสินใจและเหตุผล / แตะเมื่อตัดสินใจใหม่
- `docs/conversation.md` — แบบเก่า: การคุยแชทต่อเนื่อง
- `docs/data-model.md` — แบบเก่า: รูปแบบการเก็บข้อมูล
- `docs/migration-map.md` — แบบเก่า: ไฟล์จาก line_ (MVP) ย้ายไปไหน
- `docs/open-questions.md` — แบบเก่า: เรื่องที่ยังไม่รู้
- `docs/runbook.md` — แบบเก่า: คู่มือเมื่อระบบมีปัญหา (โครง)
- `docs/softnix-questions.md` — แบบเก่า: คำถามที่ต้องส่ง Softnix
- `docs/diagrams/` — แผนภาพ HTML 6 ภาพของฉบับ 3 (ระบบ, flow, สถานะ, call_ai, ทางด่วน, ชั้นโค้ด) / เปิด index.html

## app/
- `app/main.py` — FastAPI app factory ต่อ router / ไม่มี business logic
- `app/config.py` — อ่าน env จุดเดียว fail fast / แตะเมื่อเพิ่ม env
- `app/logging.py` — structlog JSON + id ตามย้อน
- `app/api/` — HTTP endpoints (บาง ไม่มี logic)
- `app/api/webhook.py` — POST /webhook: บันทึก event, ใส่คิว, ตอบ 200 ทันที
- `app/api/fast_path.py` — ทางด่วนคำสั่งเปลี่ยนสถานะ ไม่รอล็อกต่อ user (เสนอ)
- `app/api/close.py` — GET /close?token=: ลิงก์ครั้งเดียวให้พนักงานปิดเคส
- `app/api/health.py` — GET /health: เช็ค Postgres / Redis
- `app/worker/` — arq worker: งานจากคิว, cron, ล็อก
- `app/worker/settings.py` — WorkerSettings ของ arq (งาน, cron, Redis)
- `app/worker/tasks.py` — process_event(raw_event_id): งานหลักต่อ event
- `app/worker/locks.py` — ล็อกต่อ user ให้ทำทีละข้อความ
- `app/worker/cron.py` — งานเบื้องหลังตามเวลา
- `app/pipeline/` — ลำดับขั้นตัดสินใจ (ไม่เรียก LINE ไม่เขียน SQL)
- `app/pipeline/orchestrator.py` — เรียก step ตามลำดับ บันทึก ai_steps
- `app/pipeline/schemas.py` — ชนิดข้อมูล Pydantic v2 ของ step
- `app/pipeline/handoff.py` — start/end_handoff: จุดเดียวเปลี่ยนสถานะ bot <-> human
- `app/pipeline/steps/` — แต่ละขั้นของ pipeline
- `app/pipeline/steps/prepare.py` — ขั้น 3: เตรียมข้อมูล
- `app/pipeline/steps/handoff_check.py` — ขั้น 4: คำสั่งคืน AI / ขอพนักงาน -> HandoffResult
- `app/pipeline/steps/rules.py` — ขั้น 5: กฎในโค้ดก่อน AI (ใช้ prompts/rules/)
- `app/pipeline/steps/call_ai.py` — ขั้น 6: เรียก AI API ครั้งเดียว
- `app/pipeline/steps/update_state.py` — ขั้น 7: อัปเดต conversation_state
- `app/pipeline/steps/validate.py` — ขั้น 8: ตรวจคำตอบก่อนส่ง
- `app/pipeline/steps/send.py` — ขั้น 9: sendGuard ด่านเดียวของข้อความขาออก
- `app/pipeline/steps/fallback.py` — คำตอบสำรองเมื่อ AI ล่ม / อ่านผลไม่ออก
- `app/privacy/` — ข้อมูลส่วนบุคคล
- `app/privacy/redact.py` — ปิด PII ก่อนส่ง AI และก่อนลง log
- `app/repositories/` — ที่เดียวที่เขียน SQL (หนึ่งไฟล์ต่อกลุ่มตาราง)
- `app/repositories/raw_events.py` — line_raw_events
- `app/repositories/users.py` — line_users, line_consents
- `app/repositories/conversations.py` — conversations
- `app/repositories/messages.py` — messages, attachments
- `app/repositories/state.py` — conversation_state
- `app/repositories/ai_runs.py` — ai_runs, ai_steps
- `app/repositories/feedback.py` — feedback
- `app/services/` — client ภายนอกและตัวช่วย
- `app/services/line_client.py` — LINE Messaging API, ที่เดียวที่ถือ token
- `app/services/ai_client.py` — AI API ภายนอก, ที่เดียวที่รู้รูปแบบ API (รอคำตอบ)
- `app/services/ai_parsers.py` — แปลงคำตอบดิบ AI เป็น AiResult
- `app/services/flex_builder.py` — สร้างข้อความ LINE ด้วยโค้ด (ไม่ใช่ AI)
- `app/services/kill_switch.py` — ปิดบอทให้ call center รับทั้งหมด ไม่ต้อง deploy
- `app/services/notify.py` — แจ้ง call center เมื่อส่งต่อ/คืน AI, เวลาทำการ
- `app/services/storage.py` — เก็บ/ลบรูปใน MinIO

## tests/
- `tests/` — เทสต์แบบใหม่ (Python) / ต่างจาก `test/` แบบเก่า
- `tests/pipeline/test_handoff_check.py` — เคสของ handoff_check (ยังไม่มีฟังก์ชัน)
- `tests/services/test_ai_client.py` — เคสของ ai_client/ai_parsers ด้วย respx (ยังไม่มีฟังก์ชัน)
- `tests/scenarios/` — regression 30 บทสนทนา (ยังไม่มีข้อมูล)

## prompts/
- `prompts/system.md` — system prompt (รอรูปแบบ AI API)
- `prompts/rules/` — รายการคำของกฎในโค้ด / ทุกคำต้องมีเคสทดสอบ
- `prompts/rules/escalate.txt` — คำที่ส่งต่อพนักงานทันที (rules.py)
- `prompts/rules/faq.txt` — FAQ / ทักทายที่ตอบเอง + keyword ที่ Vendor 1 ตอบ (rules.py)
- `prompts/rules/resume_ai.txt` — คำสั่ง "คุยกับ AI" ตรงทั้งข้อความ (handoff_check.py)

## migrations/
- `migrations/` — schema Postgres 16 เป็น .sql รันด้วย dbmate (ยังไม่มีไฟล์)

## แบบเก่า (Node — ห้ามแก้ ห้ามลบ ห้ามย้าย)
- `src/` — แบบเก่า: โค้ด Node (stub) / อ้างอิงเท่านั้น
- `dev/` — แบบเก่า: debug routes และ stubs
- `test/` — แบบเก่า: เทสต์ JS
- `deploy/` — แบบเก่า: pm2 ecosystem.config.js
