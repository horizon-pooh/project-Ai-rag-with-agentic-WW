# filemap

## ราก
- `CLAUDE.md` — กติกาสำหรับ agent / อ่านทุกครั้ง
- `README.md` — แบบเก่า (ติดป้ายแล้ว) / ไม่ต้องแตะ
- `.env.example` — รายชื่อ env ที่ต้องตั้ง / แตะเมื่อเพิ่ม config
- `pyproject.toml` — dependency และเครื่องมือ Python / แตะเมื่อเพิ่ม package
- `docker-compose.yml` — service ตาม Stack + migrate (dbmate) + healthcheck/ลำดับเริ่ม (ยังไม่ทดสอบบน Docker จริง) / แตะเมื่อเพิ่ม service
- `compose.debug.yml` — เปิดพอร์ต Postgres 5432 / MinIO console 9001 ที่ 127.0.0.1 ไว้ดูข้อมูลตอนทดสอบ (ใช้คู่กับ docker-compose.yml) / แตะเมื่ออยากดู service อื่นจากเครื่องตัวเอง
- `Dockerfile` — image เดียวของ api และ worker (uv sync ตาม uv.lock) / แตะเมื่อเปลี่ยนวิธี build
- `.dockerignore` — ไฟล์ที่ไม่ใส่ใน image / แตะเมื่อเพิ่มโฟลเดอร์ใหญ่
- `.python-version` — pin Python 3.12 ให้ uv / แตะเมื่อเปลี่ยนเวอร์ชัน
- `uv.lock` — lock dependency (สร้างด้วย `uv lock`) / ไม่แก้มือ
- `Caddyfile` — HTTPS หน้า api เปิดเฉพาะ /webhook /health /close /static/no-image.png (โดเมนรอคำตอบ) / แตะเมื่อเพิ่ม route สาธารณะ

## docs/
- `docs/Line_ai_bot.md` — แบบหลัก: สถาปัตยกรรม, flow, หลักการ, Stack / อ่านก่อนทำงานทุกครั้ง
- `docs/PLAN.md` — แผนงานรอบแรก + สัญญาระหว่างโมดูล + เรื่องค้าง / อ่านก่อนแก้โค้ด
- `docs/decisions.md` — บันทึกการตัดสินใจและเหตุผล / แตะเมื่อตัดสินใจใหม่
- `docs/test-deploy.md` — ขึ้นระบบทดสอบบนเครื่องจริง (LINE OA ทดสอบ, AI stub) + เช็คลิสต์ + วัดเวลา (ยังไม่ได้ลอง) / แตะเมื่อ flow หรือคำสั่งเปลี่ยน
- `docs/conversation.md` — แบบเก่า: การคุยแชทต่อเนื่อง
- `docs/data-model.md` — แบบเก่า: รูปแบบการเก็บข้อมูล
- `docs/migration-map.md` — แบบเก่า: ไฟล์จาก line_ (MVP) ย้ายไปไหน
- `docs/open-questions.md` — แบบเก่า: เรื่องที่ยังไม่รู้
- `docs/runbook.md` — แบบเก่า: คู่มือเมื่อระบบมีปัญหา (โครง)
- `docs/softnix-questions.md` — แบบเก่า: คำถามที่ต้องส่ง Softnix
- `docs/diagrams/` — แผนภาพ HTML 9 ภาพ (ระบบ, flow, สถานะ, call_ai, ทางด่วน, ชั้นโค้ด, deploy, ภาพรวมอ่านง่าย, high-level) อัปเดต 26 ต.ค. 2026 / เปิด index.html

## app/
- `app/main.py` — FastAPI app factory ต่อ router / ไม่มี business logic
- `app/config.py` — อ่าน env จุดเดียว fail fast / แตะเมื่อเพิ่ม env
- `app/logging.py` — structlog JSON + id ตามย้อน
- `app/api/` — HTTP endpoints (บาง ไม่มี logic)
- `app/api/webhook.py` — POST /webhook: บันทึก event, ใส่คิว, ตอบ 200 ทันที
- `app/api/fast_path.py` — ทางด่วนคำสั่งเปลี่ยนสถานะ ไม่รอล็อกต่อ user (เสนอ)
- `app/api/close.py` — GET /close?token=: ลิงก์ครั้งเดียวให้พนักงานปิดเคส
- `app/api/health.py` — GET /health (liveness) และ /health/ready: เช็ค Postgres
- `app/api/assets.py` — GET /static/no-image.png: รูปแทนบนการ์ดสินค้าที่ไม่มีรูป (LINE โหลดเอง)
- `app/static/no-image.png` — ไฟล์รูปแทน 600x600 (รูปบ้านสีจาง) / แตะเมื่อเปลี่ยนรูปแทน
- `app/worker/` — worker ของคิว procrastinate (คิวบน Postgres, D-010): งานจากคิว, งาน periodic
- `app/worker/app.py` — procrastinate App, งาน process_event + งาน periodic, open_queue, run_worker / แตะเมื่อเพิ่มงานหรือเปลี่ยนรอบเวลา
- `app/worker/__main__.py` — `python -m app.worker` เริ่ม worker (คำสั่งใน docker-compose.yml)
- `app/worker/tasks.py` — process_event(raw_event_id): งานหลักต่อ event + enqueue_event (lock ต่อ user, queueing_lock ต่อ raw event)
- `app/worker/cron.py` — งานเบื้องหลังตามเวลา (requeue/release_stuck/handoff_timeout/retry_stalled_jobs, retention: raw 90 วัน / รูป / anonymize / reply token หมดอายุ)
- `app/worker/watchdog.py` — service แยก `python -m app.worker.watchdog`: เฝ้าคิวจากนอก worker (ไม่มี worker / heartbeat เก่า / pending ค้าง / DB ล่ม) แจ้งผู้ดูแลหลังพ้น grace + แจ้งหาย (D-012) / แตะเมื่อเพิ่มการตรวจ
- `app/worker/admin.py` — คำสั่งผู้ดูแล `python -m app.worker.admin` pdpa-delete / kill-switch / close-link / conversations
- `app/worker/deps.py` — ประกอบ Deps จาก Settings ครั้งเดียว (ใช้ร่วม worker และ api)
- `app/pipeline/` — ลำดับขั้นตัดสินใจ (ไม่เรียก LINE ไม่เขียน SQL)
- `app/pipeline/orchestrator.py` — เรียก step ตามลำดับ บันทึก ai_steps
- `app/pipeline/schemas.py` — ชนิดข้อมูล Pydantic v2 ของ step
- `app/pipeline/handoff.py` — start/end_handoff: จุดเดียวเปลี่ยนสถานะ bot <-> human
- `app/pipeline/pdpa.py` — ลบข้อมูลลูกค้าตาม PDPA (DB ผ่าน UsersRepo.delete_user แล้วลบไฟล์ใน Storage)
- `app/pipeline/ports.py` — Protocol ของ repositories/services ที่ pipeline รู้จัก + Deps / แตะเมื่อเพิ่มเมธอดที่ pipeline ใช้
- `app/pipeline/text.py` — normalize ข้อความ + อ่านไฟล์กฎ prompts/rules/
- `app/pipeline/replies.py` — ข้อความถึงลูกค้าทั้งหมด (ที่เดียว) + ตัวช่วย quick reply / แตะเมื่อแก้ถ้อยคำ
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
- `app/repositories/db.py` — เปิด/ปิด pool Postgres (psycopg 3 async) fail fast
- `app/repositories/flags.py` — ops_flags (เช่น kill_switch)
- `app/repositories/health.py` — ตรวจต่อ Postgres ได้ (/health/ready) + queue_health ให้ watchdog (อ่าน procrastinate_workers / pending)
- `app/repositories/raw_events.py` — line_raw_events
- `app/repositories/users.py` — line_users, line_consents, ลบตาม PDPA (delete_user)
- `app/repositories/conversations.py` — conversations
- `app/repositories/messages.py` — messages, attachments
- `app/repositories/state.py` — conversation_state
- `app/repositories/ai_runs.py` — ai_runs, ai_steps
- `app/repositories/feedback.py` — feedback
- `app/repositories/reply_tokens.py` — reply_tokens: replyToken อายุสั้น ใช้ครั้งเดียว (แทน Redis)
- `app/services/` — client ภายนอกและตัวช่วย
- `app/services/line_client.py` — LINE Messaging API, ที่เดียวที่ถือ token
- `app/services/ai_client.py` — AI API ภายนอก (gateway แบบ OpenAI chat, D-011), ที่เดียวที่รู้รูปแบบ API
- `app/services/ai_parsers.py` — แปลงคำตอบดิบ AI เป็น AiResult (MarkdownParser ตั้งต้น / JsonParser)
- `app/services/flex_builder.py` — สร้างข้อความ LINE ด้วยโค้ด (ไม่ใช่ AI)
- `app/services/kill_switch.py` — ปิดบอทให้ call center รับทั้งหมด ไม่ต้อง deploy
- `app/services/alert.py` — แจ้งผู้ดูแลระบบ (ALERT_CHANNEL: log + Sentry / email ผ่าน SMTP) ไม่ใช่ LINE / แตะเมื่อเพิ่มช่องทาง
- `app/services/notify.py` — แจ้ง call center เมื่อส่งต่อ/คืน AI, เวลาทำการ
- `app/services/storage.py` — เก็บ/ลบรูปใน MinIO

## tests/
- `tests/` — เทสต์แบบใหม่ (Python) / ต่างจาก `test/` แบบเก่า
- `tests/fakes.py` — fake ในหน่วยความจำของทุกพอร์ตใน ports.py / แตะเมื่อเพิ่มเมธอดในพอร์ต
- `tests/infra_fakes.py` — คิว procrastinate บน InMemoryConnector, run_turn ปลอม (api / worker)
- `tests/test_config.py`, `tests/test_fakes.py`, `tests/test_smoke.py` — config, ความถูกของ fake, smoke
- `tests/pipeline/` — ทีละ step + orchestrator + handoff + ไฟล์กฎ (`test_rule_files.py`)
- `tests/services/` — client ภายนอกด้วย respx (`_helpers.py` ตัวช่วยร่วม)
- `tests/services/test_ai_parsers_markdown.py` — MarkdownParser กับคำตอบจริง + เคสสังเคราะห์
- `tests/services/fixtures/ai_responses/` — คำตอบจริงของ AI API (2026-10-23) `_meta` = status, latency_s, request_messages / ข้อมูลแคตตาล็อกสาธารณะ ไม่มีข้อมูลลูกค้า
- `tests/api/` — webhook, /close, /health ผ่าน create_app + fake (`_app.py`)
- `tests/worker/` — tasks, cron, app (คิว/งาน periodic/worker ในหน่วยความจำ), deps, admin CLI
- `tests/privacy/` — รูปแบบ redact
- `tests/repositories/` — SQL: ตรวจแบบ static เสมอ + integration ข้ามถ้าไม่ตั้ง `TEST_DATABASE_URL` (รวม migrations ขึ้น-ลง, reply_tokens, คิว procrastinate บน Postgres จริง)
- `tests/scenarios/` — ตัวรัน regression + `cases/*.toml` (สังเคราะห์ 3 เคส, แชทจริงยังไม่มี)

## prompts/
- `prompts/system.md` — system prompt (รอรูปแบบ AI API)
- `prompts/rules/` — รายการคำของกฎในโค้ด / ทุกคำต้องมีเคสทดสอบ
- `prompts/rules/escalate.txt` — คำที่ส่งต่อพนักงานทันที (rules.py)
- `prompts/rules/faq.txt` — FAQ / ทักทายที่ตอบเอง + keyword ที่ Vendor 1 ตอบ (rules.py)
- `prompts/rules/resume_ai.txt` — คำสั่ง "คุยกับ AI" ตรงทั้งข้อความ (handoff_check.py)

## migrations/
- `migrations/` — schema Postgres 16 เป็น .sql รันด้วย dbmate
- `migrations/20261022000000_init.sql` — schema แรก (ร่าง ต้องเทียบ line_chat_schema_v2.sql) / แตะเมื่อเปลี่ยนตาราง (เพิ่มไฟล์ใหม่ ไม่แก้ไฟล์ที่รันแล้ว)
- `migrations/20261023000000_retention_indexes.sql` — partial index ของคิวรี retention/anonymize (messages, ai_steps, attachments) / แตะเมื่อแก้ WHERE ของคิวรีเหล่านั้น
- `migrations/20261024000000_procrastinate_schema.sql` — schema ของ procrastinate 3.10.0 (คัดลอกจากแพ็กเกจ ห้ามแก้มือ) / อัปเกรด procrastinate = เพิ่ม migration ใหม่ตามหัวไฟล์
- `migrations/20261024000100_reply_tokens.sql` — ตาราง reply_tokens (แทน Redis)

## แบบเก่า (Node — ห้ามแก้ ห้ามลบ ห้ามย้าย)
- `src/` — แบบเก่า: โค้ด Node (stub) / อ้างอิงเท่านั้น
- `dev/` — แบบเก่า: debug routes และ stubs
- `test/` — แบบเก่า: เทสต์ JS
- `deploy/` — แบบเก่า: pm2 ecosystem.config.js
- `deploy/backup/` — สำรอง/ซ้อมกู้ Postgres (D-012): `pg_backup.sh` (cron ทุกคืน, gpg, ส่งออกนอก VM), `restore_drill.sh`, README วิธีตั้งและกู้จริง / แตะเมื่อเปลี่ยนวิธีสำรอง
