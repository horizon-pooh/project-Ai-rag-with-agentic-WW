# PLAN: ลงมือเขียนแบบใหม่ (Python)

อ้างอิง: `docs/Line_ai_bot.md` (แบบหลัก) — ไฟล์นี้เป็นแผนงานและสัญญาระหว่างโมดูลที่ orchestrator ตัดสินไว้ให้ worker
อะไรที่ขัดกับแบบหลัก ให้ยึดแบบหลักและรายงาน อะไรที่ขึ้นกับ AI API ที่ยังไม่รู้ ใส่ป้าย "รอคำตอบ"

## ขอบเขตรอบนี้

แผน 1 สัปดาห์ วัน 1–5 ในโค้ด + เทสต์หน่วยที่รันได้โดยไม่ต้องมี Postgres/Redis/LINE/AI จริง
ไม่ทำรอบนี้: consent flow เต็ม, retention job ลบไฟล์จริง, Sentry/Uptime Kuma, Jev/Laya shadow, ข้อมูลแชทจริง 30 บทสนทนา (ทำแค่ตัวรันและตัวอย่างสังเคราะห์)

## ข้อตัดสินของ orchestrator (เสนอ — บันทึกใน `docs/decisions.md` D-009)

| เรื่อง | ตัดสิน |
|---|---|
| Python | 3.12 (`uv python pin 3.12`) |
| DB driver | psycopg 3 async + psycopg_pool (`AsyncConnectionPool`) |
| LINE | เรียกด้วย httpx ใน `services/line_client.py` (จำลองด้วย respx ได้) ตรวจ signature ด้วย `hmac` + base64 เอง ไม่ใช้ line-bot-sdk |
| schema | ไม่มี `line_chat_schema_v2.sql` ใน repo → เขียน migration ร่างจากแบบหลัก ติดป้าย "ร่าง ต้องเทียบกับไฟล์จริง" |
| kill switch | ตาราง `ops_flags` ใน Postgres (เปิด/ปิดไม่ต้อง deploy) OR กับ env `KILL_SWITCH` |
| replyToken | Redis key `replytoken:{raw_event_id}` TTL = `REPLY_TOKEN_TTL_S` (เสนอ 50) ใช้แล้ว GETDEL; ไม่มี/หมดอายุ → push |
| ล็อกต่อ user | Redis `SET lock:user:{line_user_id} <token> NX PX` TTL = `TURN_DEADLINE_S + 30` วิ ต่ออายุทุก 10 วิระหว่างรอ ปลดล็อกด้วย compare-and-delete (Lua) |
| claim event | `UPDATE line_raw_events SET process_status='processing', claimed_at=now() WHERE id=$1 AND process_status='pending' RETURNING ...` ใครได้แถวคนนั้นทำ (worker และ fast path ใช้ทางเดียวกัน) |
| ตัวนับสัญญาณหลังเกิดเหตุ | `conversation_state.counters` (jsonb, ไม่ส่ง AI): `clarify_streak`, `cannot_answer_streak`, `validate_fail_streak`, `last_products` |
| เกณฑ์ (เสนอ ปรับจากข้อมูลจริง) | clarify ติดกันครบ `CLARIFY_MAX=2` แล้วยัง clarify → ส่งต่อ; cannot_answer ครั้งแรก → เสนอปุ่ม, ติดกันครั้งที่ 2 → ส่งต่อ; validate ไม่ผ่านครั้งแรก → ถามกลับ, ติดกันครั้งที่ 2 → ส่งต่อ; ถี่ผิดปกติ > `RATE_LIMIT_PER_MIN=10` ข้อความ/นาที |
| การจับคำ | `resume_ai.txt` และ `faq.txt` ตรงทั้งข้อความหลัง normalize; `escalate.txt` มีอยู่ในข้อความ (substring) หลัง normalize |
| รูปแบบ `faq.txt` | `pattern<TAB>คำตอบ`; คำตอบเป็น `-` = keyword ของ Vendor 1 (บอทไม่ตอบ) |
| แจ้ง call center | ช่องทางรอคำตอบ → `LogNotifier` (log อย่างเดียว) หลัง interface `Notifier` |
| AI API | สัญญาเสนอ (รอคำตอบ): POST `AI_API_URL` JSON ของ `AiRequest`, Bearer `AI_API_KEY`, ตอบ JSON ตามฟิลด์ `AiResult` → `JsonParser`; `MarkdownParser` = stub ที่ยก `NotImplementedError("รอคำตอบ: ...")`; รูปยังไม่ส่ง (รอคำตอบ) |

## สัญญาระหว่างโมดูล

### ค่าคงที่
- `conversations.status`: `bot` | `human`
- `line_raw_events.process_status`: `pending` | `processing` | `done` | `failed` | `skipped`
- `handoff_end_reason`: `user_resumed` | `timeout` | `staff_closed`
- postback data (querystring): `action=talk_to_staff`, `action=resume_ai`, `action=restart`, `action=detail&sku=<SKU>`, `action=feedback&v=up|down&run=<ai_run_id>`
- ขีดจำกัด LINE ที่ใช้ใน validate: ≤5 ข้อความต่อ reply/push, text ≤5000 ตัวอักษร, carousel ≤10 bubble (เราจำกัดเอง ต่ำกว่าเพดาน LINE 12), quick reply ≤13 รายการ, altText ≤400

### `app/pipeline/text.py`
- `normalize(s) -> str`: NFC → ลบ zero-width (U+200B, U+200C, U+200D, U+2060, U+FEFF) → ลบ whitespace ทั้งหมด → casefold
- `load_rule_lines(path) -> list[str]`: ข้ามบรรทัดว่างและ `#`, normalize ทุกบรรทัด
- `load_faq(path) -> dict[str, str | None]`: `pattern<TAB>answer`, `-` → `None` (Vendor 1)

### `app/pipeline/ports.py` (typing.Protocol) — pipeline รู้จักแค่นี้
repositories และ services ต้อง implement ตามนี้ เทสต์ใช้ fake ใน `tests/fakes.py`
ชื่อเมธอดตัดสินโดยงาน 2 แล้วถือเป็นสัญญา (ห้ามงานอื่นแก้ ถ้าต้องเปลี่ยนให้รายงาน)

ข้อยกเว้นเรื่องชั้น: `app/pipeline/schemas.py` เป็นโมดูลข้อมูลล้วน (import แค่ stdlib + pydantic) ชั้นไหนก็ import ได้
รวมทั้ง error `AiUnavailable` / `AiBadResponse` ที่ services ยกและ pipeline จับ — เพราะแบบหลักให้ `ai_client` คืน `AiResult`

พอร์ต (ชื่อเมธอดเป็นแนวทาง งาน 2 ปรับได้):
- `RawEventsRepo`: insert (กันซ้ำ คืน None ถ้าซ้ำ), get, claim (pending→processing แบบมีเงื่อนไข), mark_done(status), mark_failed, list_pending_ids_for_user, list_stale (ให้ cron)
- `UsersRepo`: get_or_create, has_consent, get_line_user_id(user_id), delete_user(line_user_id) → storage keys | None (PDPA, R2-A)
- `ConversationsRepo`: get, get_or_open (bot เงียบเกิน STATE_TTL → ปิดแล้วเปิดใหม่; human ห้ามเปิดใหม่), touch_user_message, start_handoff (WHERE status='bot'), end_handoff (WHERE status='human' AND handoff_started_at=$3), list_handoff_timed_out, create_close_token, consume_close_token (ใช้ครั้งเดียว เก็บแค่ hash)
- `StateRepo`: get (คืนแถวพร้อม expires_at ให้ prepare ตัดสินว่าหมดอายุ), save(state, counters, expires_at), clear
- `MessagesRepo`: add_inbound, recent_texts_redacted, count_inbound_since, reserve_outbound(dedupe_key) → (message_id, already_sent), mark_outbound(status, reason), add_attachment, list_expired_attachments, mark_attachment_deleted, anonymize_older_than (retention, R2-A)
- `AiRunsRepo`: create_run, add_step, finish_run(route, reason, confidence), anonymize_steps_older_than (retention, R2-A)
- `FlagsRepo`: get_flag / set_flag (kill switch)
- `AiClient.call(req, deadline_s) -> AiResult` (ยก AiUnavailable / AiBadResponse)
- `LineClient`: reply, push, start_loading, get_content
- `ReplyTokens`: put, take (GETDEL)
- `Notifier`: handoff_started(conv, summary, close_url), handoff_ended(conv, reason)
- `Storage`: put_image, delete
- `KillSwitch.is_on()`
- `Clock.now()` (เทสต์ควบคุมเวลาได้)
- `Deps` (dataclass) รวมทุกพอร์ต + `Settings` ส่งเข้า orchestrator

### `app/pipeline/schemas.py` (Pydantic v2)
`InboundEvent`, `Conversation`, `StateRecord`, `Product`, `AiRequest`, `AiResult`, `HandoffOutcome`/`HandoffResult`, `RuleOutcome`/`RuleResult`, `OutboundMessage`, `SendResult`, `ValidationResult` — ฟิลด์ตามหัวข้อ 4 ของแบบหลัก

### ข้อความถึงลูกค้า (`app/pipeline/replies.py` ค่าคงที่ที่เดียว)
| key | ข้อความ |
|---|---|
| `RESUMED` | กลับมาคุยกับผู้ช่วย AI แล้วค่ะ ต้องการหาสินค้าอะไรคะ |
| `ALREADY_BOT` | ตอนนี้กำลังคุยกับผู้ช่วย AI อยู่แล้วค่ะ ต้องการหาสินค้าอะไรคะ |
| `HANDOFF_IN_HOURS` | รับเรื่องแล้วค่ะ กำลังส่งต่อให้เจ้าหน้าที่ เจ้าหน้าที่จะตอบกลับในแชทนี้ค่ะ |
| `HANDOFF_OUT_OF_HOURS` | รับเรื่องแล้วค่ะ ตอนนี้อยู่นอกเวลาทำการ ({hours}) เจ้าหน้าที่จะตอบกลับในเวลาทำการค่ะ |
| `HANDOFF_TIMED_OUT_PREFIX` | หมดเวลาคุยกับเจ้าหน้าที่แล้ว ตอนนี้กลับมาคุยกับผู้ช่วย AI ค่ะ |
| `STATE_EXPIRED` | ข้อมูลที่คุยไว้ก่อนหน้าหมดอายุแล้วค่ะ ช่วยบอกอีกครั้งว่าต้องการหาสินค้าอะไรคะ (+ quick reply "เริ่มใหม่" → `action=restart`) |
| `RESTARTED` | เริ่มใหม่แล้วค่ะ ต้องการหาสินค้าอะไรคะ |
| `AI_DOWN` | ขออภัยค่ะ ระบบตอบกลับขัดข้องชั่วคราว (+ quick reply "คุยกับเจ้าหน้าที่" → `action=talk_to_staff`) |
| `CANNOT_ANSWER` | ขออภัยค่ะ เรื่องนี้ยังตอบให้ไม่ได้ ต้องการคุยกับเจ้าหน้าที่ไหมคะ (+ quick reply คุยกับเจ้าหน้าที่) |
| `VALIDATE_RETRY` | ขอโทษค่ะ ช่วยบอกรายละเอียดเพิ่มอีกนิดได้ไหมคะ เช่น ประเภทหรือรุ่นสินค้า |
| `SEARCHING` | กำลังค้นหาให้ค่ะ รอสักครู่นะคะ |
| `RATE_LIMITED` | ได้รับข้อความแล้วค่ะ ขอเวลาสักครู่นะคะ |
| `EMPTY` | ต้องการให้ช่วยหาสินค้าอะไรคะ |
| `DETAIL_NOT_FOUND` | ขออภัยค่ะ ไม่พบรายละเอียดสินค้านี้แล้ว ช่วยค้นหาใหม่อีกครั้งนะคะ |

การ์ดสินค้า (`services/flex_builder.py`): bubble ต่อสินค้า = รูป (ถ้ามี `image_url`), ชื่อ, ราคา `฿{price:,.0f}` (ไม่มีราคา → ไม่แสดงบรรทัดราคา), ปุ่ม "ดูรายละเอียด" (postback `action=detail&sku=`), ปุ่ม "เปิดหน้าสินค้า" (uri = `url`). intro ของ AI ส่งเป็น text นำหน้า carousel

## งาน

| # | งาน | tier | รอ |
|---|---|---|---|
| 1 | Scaffold: pin 3.12, dependency, Dockerfile, pytest/ruff ผ่าน | light | – |
| 2 | Contracts: migration ร่าง, config, logging, schemas, ports, text, replies, tests/fakes | careful | 1 |
| 3 | privacy/redact + ไฟล์กฎเริ่มต้น + เทสต์ไทยทุกคำ | careful | 2 |
| 4 | handoff, handoff_check, rules (+ เทสต์ตาม docstring ของ test_handoff_check.py) | careful | 2 |
| 5 | call_ai, update_state, validate, fallback, send (sendGuard), flex_builder | careful | 2 |
| 6 | services: ai_client, ai_parsers, line_client, kill_switch, notify, storage (respx) | careful | 2 |
| 7 | repositories: SQL ทั้งหมด + integration test ที่ข้ามถ้าไม่มี `TEST_DATABASE_URL` | careful | 2 |
| 8 | prepare, orchestrator, fast path, worker (tasks/locks/cron/settings), api, main + เทสต์ end-to-end ด้วย fake | heavy | 3–7 |
| 9 | ตัวรัน scenarios + ตัวอย่างสังเคราะห์ 3 บทสนทนา | medium | 8 |
| 10 | เอกสาร: filemap, decisions D-009, READMEs, docker-compose command | light | 9 |

กติกาทุกงาน: ไม่แตะ `src/ dev/ test/ deploy/`; ไม่แก้ `pyproject.toml` (ยกเว้นงาน 1) ไม่แก้ `filemap.md` (งาน 10); ส่งต่อ docstring เดิมของ stub (หัวไฟล์) ไว้ แก้บรรทัด "สถานะ" ให้ตรงความจริง; `uv run pytest` และ `uv run ruff check .` ต้องผ่าน

## สถานะ

- [x] 1 [x] 2 [x] 3 [x] 4 [x] 5 [x] 6 [x] 7 [x] 8 [x] 9 [x] 10

## ค้าง / ติดตามต่อ

| เรื่อง | รายละเอียด |
|---|---|
| schema | ร่าง ต้องเทียบ `line_chat_schema_v2.sql` เมื่อได้ไฟล์ |
| repositories | รันกับ Postgres 16.2 จริงแล้ว (2026-10-23, pgserver บนเครื่อง dev) ผ่านทั้งหมด — ยังไม่ได้ลองกับข้อมูลปริมาณจริง (แผนของ index retention ยืนยันแค่บนตารางว่าง) |
| docker compose | ยังไม่ได้ลอง (เครื่องนี้ไม่มี docker) |
| scenarios | ข้อมูลแชทจริง 30 บทสนทนา ยังไม่มี |
| AI API | สัญญารอคำตอบ ทั้ง 4 ข้อ (Softnix ไหม, JSON/markdown, คืน state ไหม, รับรูป/บอกส่งต่อได้ไหม) |
| validate | intro ของ AI ส่งตรง ไม่ตรวจราคา/ลิงก์ในข้อความ |
| Caddy | `/health/ready` ไม่เปิดผ่าน Caddy |
| dependency | redis เป็น dependency ทางอ้อมผ่าน arq |
| cron | cron ของ arq ใช้เวลาของ container |
| เครื่องนี้ | ใช้ `uv run python -m pytest` เพราะ pytest.exe ถูกบล็อก |
| ขอพนักงาน | คำสั่งแบบพิมพ์จับแบบ substring ไม่สนคำปฏิเสธ ("ไม่ต้องคุยกับพนักงาน" ยังส่งต่อ) |
