# บันทึกการตัดสินใจ

แต่ละข้อบอกว่าตัดสินใจอะไร เพราะอะไร และถ้าจะเปลี่ยนใจต้องเกิดอะไรขึ้นก่อน
ข้อที่ยังไม่ได้รับการยืนยันจากเจ้าของโปรเจกต์ติดป้าย **รอยืนยัน**

---

## D-001 ทำในโฟลเดอร์ใหม่ `line_prod` ไม่แก้ `line_`

- **สถานะ:** ยืนยันแล้ว (2026-10-21)
- `line_` ยังเป็น MVP ที่ทำงานได้และมีเทสต์ครบ ใช้เป็นต้นฉบับย้ายโค้ด
- **ย้ายโค้ดที่ผ่านเทสต์แล้ว ไม่เขียนใหม่** (ดู `migration-map.md`)
- **สถานะ (22 ต.ค.): แทนที่บางส่วน** — ทำในโฟลเดอร์ใหม่ยังใช้; "ย้ายโค้ด JS ไม่เขียนใหม่" ไม่ใช้ถ้าเป็น Python (ดู D-008)

## D-002 รันบน server/VM ของบริษัท ด้วย pm2 + Redis เครื่องเดียวกัน

- **สถานะ:** ยืนยันแล้ว (2026-10-21)
- ปริมาณ 4–5 พันข้อความ/วัน server เดียวรับได้สบาย
- Redis ทำให้ข้อมูลไม่หายตอน restart และเก็บคิว Softnix ได้
- **สถานะ (22 ต.ค.): แทนที่บางส่วน** — pm2 → Docker Compose (เสนอ); Redis เครื่องเดียวกันยังใช้แต่เป็นคิวอย่างเดียว ข้อมูลอยู่ Postgres (ดู D-008)
- **สถานะ (23 ต.ค.): Redis แทนที่** — ไม่ใช้ Redis แล้ว คิวย้ายไป Postgres (ดู D-010)

## D-003 ระบบเราเป็นผู้ส่งข้อความเข้า LINE แต่เพียงผู้เดียว (Softnix ไม่ส่งเอง)

- **สถานะ: รอยืนยัน** — ข้อนี้ **กลับคำตัดสินเดิม** ที่เลือกให้ Softnix ส่ง LINE เอง
- **เหตุผล** (ที่ปรึกษาทั้ง 5 มุมมองเห็นตรงกัน):
  1. **ผิดกฎหลัก "AI ห้ามตอบตอนอยู่กับเจ้าหน้าที่"** — ลูกค้าถามสินค้า → รอ 90 วิ → พิมพ์ "ขอคุยพนักงาน" → ถูกส่งต่อคน → วินาทีที่ 200 Softnix ส่งการ์ดสินค้าเข้าแชทที่เจ้าหน้าที่ดูแลอยู่ ระบบเราหยุดไม่ได้เพราะไม่ได้เป็นคนส่ง
  2. token ไปอยู่ 2 ที่ โอกาสรั่วเพิ่มเป็นสองเท่า และ token นี้ส่งหาลูกค้าทุกคนของ OA ได้
  3. ตรวจคำตอบก่อนถึงลูกค้าไม่ได้ (เช่น ราคาผิด)
  4. ลูกค้าบอก "ได้คำตอบสองรอบ" หรือ "ไม่ได้อะไรเลย" ต้องไล่ log สองระบบ
  5. ต้องพึ่งความสามารถของ Softnix 4 อย่างที่ยังไม่มีใครยืนยัน
- **ผลต่อโครงสร้าง:** ตัด route `softnix-callback` และสวิตช์ `AI_DELIVERY` ออก — ทุกข้อความออกทาง `src/line/sendGuard.js`
- **สถานะ (22 ต.ค.): ยังใช้** — sendGuard ย้ายเข้าแบบใหม่ (ขั้น send ใน `docs/Line_ai_bot.md`)
- **จะกลับไปให้ Softnix ส่งเองได้ก็ต่อเมื่อ:** Softnix ตอบ "ได้" ครบทั้ง 4 ข้อใน `softnix-questions.md` **และ** ยอมถามระบบเราก่อนส่งทุกครั้งว่า "ยังส่งได้ไหม" (เพื่อให้กฎข้อ 1 ยังจริง)

## D-004 ตัดออกจากเวอร์ชันแรก (ย้ายมาทีหลังได้)

- **สถานะ:** รอยืนยัน
- **เหตุผล:** คนพัฒนาคนเดียว ต้องสร้าง เทสต์ และดูแลได้จริง
- **สถานะ (22 ต.ค.): แทนที่** — รายการไฟล์ MVP ข้างล่างไม่เกี่ยวแล้ว ขอบเขตดูแผน 1 สัปดาห์ใน `docs/Line_ai_bot.md`

| ตัดอะไร | ไฟล์ใน MVP | ทำไมตัดได้ตอนนี้ |
|---|---|---|
| รวมข้อความที่พิมพ์ติดกัน | `messageBatcher.js` | คิว Softnix ใหม่มีกฎ "งานใหม่แทนงานเก่า" ครอบคลุมเคสหลักแล้ว |
| ถามกลับก่อนค้น | `clarifier.js` | ปิดอยู่แล้วใน MVP (`CLARIFY_ENABLED=false`) |
| cache คำตอบ | `answerCache.js` | ราคาและสต็อกเปลี่ยน cache อาจบอกราคาผิด ต้องคิดเรื่องอายุ cache ก่อน |
| นับโทเคน | `tokenCounter.js` | จำกัด history ด้วยจำนวนข้อความแทนได้ |
| แบบประเมิน | `survey.js` | ไม่กระทบการตอบลูกค้า |
| dashboard / ioLog / traceLog | `dashboard.html`, `ioLog.js`, `traceLog.js` | ใช้ตอน dev — ใช้ `ops/dailyReport` แทน |
| chaos tests | `test/chaos/` | ย้ายมาเมื่อระบบหลักนิ่งแล้ว |

## D-005 คิว Softnix ต้อง "ปฏิเสธเป็น" ไม่ใช่แค่ให้รอ

- **สถานะ:** รอยืนยัน (ตัวเลขยังเป็นค่าเสนอ)
- **เหตุผล:** Softnix ทำทีละคิว ~150 วิ → 10 ชั่วโมงทำการได้สูงสุด ~240 คำตอบ ถ้าคำถามสินค้า 10–15% ของ 4,500 = 450–675 ต่อวัน **เกินความสามารถ 2–3 เท่า**
- กฎ:
  - ตอบทันทีว่า "กำลังค้นหา อยู่คิวที่ N ประมาณ X นาที"
  - คิวยาวเกิน `SOFTNIX_MAX_QUEUE` (เสนอ 5) → ไม่รับเพิ่ม เสนอให้คุยกับเจ้าหน้าที่
  - รอเกิน `SOFTNIX_HARD_LIMIT_MS` (เสนอ 5 นาที) → เลิกรอ ส่งต่อคน
  - ลูกค้าถูกส่งต่อคน → ยกเลิกงานในคิวทันที
  - ลูกค้าถามเรื่องใหม่ → งานเก่าถูกแทน คำตอบเก่าที่มาทีหลังถูกทิ้ง
- **สถานะ (22 ต.ค.): ขึ้นกับว่า AI API คือ Softnix หรือไม่ — รอคำตอบ** (ถ้าใช่ กฎข้างล่างยังใช้กับ `call_ai`)
- **ถ้าตัวเลขจริงยืนยันว่าเกินความสามารถ:** ทำ `src/catalog/` (ค้นสินค้าจาก catalog โดยตรง ตอบในเสี้ยววินาที) แล้วส่ง Softnix เฉพาะคำถามที่ค้นตรงไม่ได้

## D-006 ต้องมีทางหลบ (kill switch)

- **สถานะ:** รอยืนยัน
- server เครื่องเดียว = ล่มเครื่องเดียวลูกค้าหายหมด
- เปิด kill switch → ระบบเราไม่ตอบอะไร ให้ call center รับทั้งหมด เปิด/ปิดได้โดยไม่ต้อง deploy
- ต้อง **ซ้อมเปิดจริง** ก่อนขึ้นระบบ
- **สถานะ (22 ต.ค.): ยังใช้** — เช็คที่ sendGuard

## D-007 ระบบเราเป็นคนเลือกว่าลูกค้าคุยกับ AI หรือ call center

- **สถานะ:** ยืนยันแล้ว (2026-10-21)
- call center กับ Vendor 1 เป็นคนละส่วนกัน
- ระบบเราเป็นคนแยกทาง: ข้อความไหนไป AI ข้อความไหนไป call center และเก็บ state ว่าตอนนี้ลูกค้าคุยกับใคร (`src/conversation/`)
- **ผล:** ไม่ต้องพึ่ง Vendor 1 บอกว่าส่งต่อคนแล้วหรือยัง (`open-questions.md` ข้อ 2 ปิดแล้ว)
- **ผลที่ต้องระวัง:** ระบบเราอยู่ตรงกลางทุกข้อความ ถ้าล่ม ลูกค้าไปถึง call center ไม่ได้เลย — ทางหลบตอน server ตาย ดู `open-questions.md` ข้อ 10
- **สถานะ (22 ต.ค.): ยังใช้** — ตรงกับสถานะ `bot`/`human` ของแบบใหม่

## D-008 ใช้ `docs/Line_ai_bot.md` เป็นแบบหลัก + AI เป็น API ภายนอก

- **สถานะ:** ยืนยันแล้ว (2026-10-22)
- `docs/Line_ai_bot.md` เป็นแบบหลัก เอกสารอื่น (21 ต.ค.) เป็นแบบเก่า ใช้ได้เฉพาะส่วนที่ติดป้าย "ยังใช้"
- AI เป็น API ภายนอก: เราส่ง state + ข้อความ (redact แล้ว) → API ค้นสินค้าเองแล้วตอบพร้อมสินค้า
- เราไม่ทำ agent, ไม่ทำ tool-calling, ไม่เรียก Search API เอง — เรียก AI API ครั้งเดียวต่อข้อความ (`call_ai`) แล้ว `update_state`
- stack Python/FastAPI/arq/Postgres/MinIO/Docker Compose **ยังเป็นข้อเสนอ** ตามหัวข้อ "ข้อเสนอที่ยังไม่ได้ยืนยัน" ของไฟล์หลัก
- **ผล:**
  - D-001 "ย้ายโค้ด JS", D-002 pm2, D-004 แทนที่ (ดูสถานะในแต่ละข้อ)
  - `data-model.md`, `migration-map.md`, แผน 4 สัปดาห์ใน README เป็นแบบเก่า
  - state 4 แบบ → `bot`/`human`; หมดเวลาส่งต่อ 3 ชม. → 6 ชม. นับจากตอนส่งต่อ
  - `src/`, `dev/`, `test/`, `deploy/ecosystem.config.js` ปล่อยไว้เป็นต้นฉบับแนวคิด ไม่ลบ ไม่แก้
  - เรื่องที่ขึ้นกับตัว API (Softnix หรือไม่, JSON/markdown, คืน state ไหม, รับรูป/บอกส่งต่อได้ไหม) **รอคำตอบ** — ดู `open-questions.md`, `softnix-questions.md`

## D-009 รายละเอียดการลงมือ (Python)

- **สถานะ:** เสนอ — รอยืนยัน (2026-10-22)
- ที่มา: ตาราง "ข้อตัดสินของ orchestrator" ใน `docs/PLAN.md` + ข้อที่ตัดสินระหว่างลงมือ

| เรื่อง | ตัดสิน | จะเปลี่ยนใจเมื่อ |
|---|---|---|
| Python / DB | 3.12, psycopg 3 async + psycopg_pool | — |
| LINE | httpx เอง ตรวจ signature ด้วย hmac ไม่ใช้ line-bot-sdk | ต้องใช้ฟีเจอร์ที่ SDK ทำให้มาก |
| schema | migration ร่างจากแบบหลัก | ได้ `line_chat_schema_v2.sql` จริง |
| kill switch | ตาราง `ops_flags` OR env `KILL_SWITCH`; อ่านค่าไม่ได้ → ถือว่าเปิด | — |
| ~~replyToken~~ | **แทนที่โดย D-010** (ตาราง `reply_tokens` ใน Postgres) — เดิม: Redis TTL `REPLY_TOKEN_TTL_S`, GETDEL; ไม่มี/หมดอายุ → push | — |
| ~~ล็อกต่อ user~~ | **แทนที่โดย D-010** (`lock` ของ procrastinate) — เดิม: Redis `SET NX PX` + ต่ออายุ + compare-and-delete; ไม่ใช้ advisory lock เพราะถือ connection ค้างระหว่างรอ AI | — |
| claim event | `UPDATE ... WHERE process_status='pending' RETURNING` ใช้ทั้ง worker และ fast path | — |
| ตัวนับ | `conversation_state.counters` (jsonb ไม่ส่ง AI) | — |
| เกณฑ์ส่งต่อ | `CLARIFY_MAX=2`, cannot_answer / validate ติดกันครั้งที่ 2, `RATE_LIMIT_PER_MIN=10` | ได้ข้อมูลแชทจริง |
| การจับคำ | resume_ai / faq ตรงทั้งข้อความ, escalate substring (หลัง normalize) | จับพลาดในแชทจริง |
| แจ้ง call center | `LogNotifier` หลัง interface `Notifier` | รู้ช่องทางจริง |
| AI API | สัญญาเสนอ JSON (`AiRequest`/`AiResult`), Markdown = stub, ยังไม่ส่งรูป — รอคำตอบ | ได้สเปก API |
| dedupe_key ขาออก | `out:<raw_event_id>:<ลำดับการส่งในรอบ>` ไม่ผูก purpose เพราะ retry อาจได้เส้นทางอื่น | — |
| /close | GET แสดงหน้ายืนยัน ปิดจริงด้วย POST (กัน link preview ใช้ token) | — |
| ~~arq~~ | **แทนที่โดย D-010** (arq maintenance-only → procrastinate) — เดิม: `keep_result=0` ให้ cron ใส่คิวซ้ำด้วย job id เดิมได้ | — |
| ไฟล์กฎ | ตัด "แอดมิน", "สินค้าเสีย", "ของเสีย", "ดีค่ะ/ดีครับ" เพราะจับผิด (ดูหัวไฟล์ใน `prompts/rules/`) | มีวิธีจับที่ไม่พลาด + เคสทดสอบ |
| redact | ที่อยู่ไม่ redact (ยังไม่มีรูปแบบที่เชื่อถือได้) | มีรูปแบบที่ทดสอบได้ |

## D-010 คิวงาน: arq + Redis → procrastinate (คิวบน Postgres) และเอา Redis ออกทั้งหมด

- **สถานะ:** ตัดสินแล้ว — เจ้าของโปรเจกต์เลือก (2026-10-23) รายละเอียดการลงมือตัดสินโดย orchestrator; ยังไม่ได้ลองบน Docker จริง
- **เหตุผล:**
  - arq ประกาศ "maintenance only mode" (issue #510, 18 ต.ค. 2025) แก้เฉพาะช่องโหว่ร้ายแรง ไม่รับแก้บั๊ก/ฟีเจอร์ใหม่ (เช็คแล้ว 2026-10-23 ดู `docs/PLAN.md`)
  - procrastinate ยังออกรุ่นใหม่ (3.10.0 ก.ย. 2026) และใช้ Postgres ที่ต้องมีอยู่แล้ว → service น้อยลงหนึ่งตัว จุดพังน้อยลงหนึ่งจุด
    และงาน + ความจริง (`line_raw_events`) อยู่ฐานเดียวกัน
  - ความเสี่ยงที่รู้: README ของ procrastinate ประกาศหาผู้ดูแลเพิ่ม ("looking for additional maintainers")

| เรื่อง | ตัดสิน |
|---|---|
| คิว | procrastinate **3.10.0** (pin ตรงตัวใน `pyproject.toml`) บน `PsycopgConnector` ใช้ pool เดียวกับ repositories (`app/worker/app.py`, `open_queue`) |
| ล็อกต่อ user | `lock` ของ procrastinate = `user:` + hash ตัดสั้นของ line_user_id (`pdpa.user_ref`) — งาน lock เดียวกันรันทีละงาน เรียงตาม priority แล้วเวลาสร้าง (เอกสาร howto/advanced/locks); ไม่ใส่ line_user_id ตัวจริงในตารางของไลบรารี (ลบตาม PDPA ไม่ต้องตามไปลบ); ลบ `app/worker/locks.py` |
| กันใส่คิวซ้ำ | `queueing_lock` = `raw:{raw_event_id}` (กันได้เฉพาะงานที่ยังรอ todo) — `AlreadyEnqueued` = อยู่ในคิวแล้ว; กันทำซ้ำจริงยังเป็น claim ใน DB |
| ลองใหม่ | `RetryStrategy(max_attempts=10, wait=10 วิ)` ทุก error ของงาน; event ลองเองได้ 3 ครั้งเหมือนเดิม (release แล้วยก `EventRetry`); ระหว่างรอ งานใหม่กว่าของ user เดียวกันรอด้วย (รักษาลำดับ) |
| เพดานเวลา | procrastinate ไม่มี timeout ในตัว → งานครอบ `asyncio.timeout(TURN_DEADLINE_S + 60)` |
| worker ตาย | heartbeat ของ procrastinate + งาน periodic `retry_stalled_jobs` ทุกนาที (เอกสาร howto/production/retry_stalled_jobs) — สำคัญเพราะงานที่ค้าง `doing` ถือ lock ของ user ไว้; `release_stuck` / `requeue_pending` ของเรายังเป็นตาข่ายฝั่งแถว |
| cron | งาน periodic ของ procrastinate (เวลา UTC) รอบเดิมของ arq + `retry_stalled_jobs` + `remove_old_jobs` (งานสำเร็จลบทันที ที่เหลือเก็บ 7 วัน) |
| replyToken | ตาราง `reply_tokens` (`expires_at = now() + REPLY_TOKEN_TTL_S`, take = `DELETE ... RETURNING` ใช้ครั้งเดียว) ลบตาม raw event (CASCADE), retention ลบที่หมดอายุ; CHECK ห้าม replyToken ใน payload คงเดิม |
| schema ของคิว | dbmate migration `20261024000000_procrastinate_schema.sql` = `schema.sql` ของ 3.10.0 ไม่แก้มือ; อัปเกรด = แก้ pin + เพิ่ม migration จากไฟล์ใหม่ใน `sql/migrations` ของแพ็กเกจ (วิธีอยู่หัวไฟล์) |
| readiness | `/health/ready` เช็ค Postgres อย่างเดียว |
| เริ่ม worker | `python -m app.worker` (`App.run_worker_async` ตอนคิวเปิดอยู่) concurrency = max(`AI_CONCURRENCY`×2, 4) |

- **จะเปลี่ยนใจเมื่อ:** procrastinate หยุดพัฒนา (ไม่มีรุ่นใหม่/ไม่แก้ช่องโหว่) หรือเจอปัญหาประสิทธิภาพของคิวบน Postgres ที่ปริมาณจริง
  (เช่น polling/LISTEN กินทรัพยากร DB, ตาราง `procrastinate_jobs` โตจน fetch ช้า)
- **ผล:** D-009 แถว replyToken / ล็อกต่อ user / arq แทนที่; D-002 ส่วน Redis แทนที่; ไม่มี service redis ใน docker compose และไม่มี `REDIS_URL`
