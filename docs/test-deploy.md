# ขึ้นระบบทดสอบบนเครื่องจริง (LINE OA ทดสอบ + AI โหมด stub)

> **ยังไม่ได้ลองบนเครื่องจริง — แก้ตามที่เจอ** (เขียนจากการอ่านโค้ดใน branch `feat/python-impl`; เครื่อง dev ไม่มี Docker)
> ที่ติดป้าย "เช็คหน้าจอจริง" = ไม่แน่ใจว่าหน้าจอ LINE ตอนนี้เป็นแบบนั้น
> เช็คกับเอกสารแล้ว (2026-10-23): image `ghcr.io/amacneil/dbmate:2` มีจริง, `--wait` / `--no-dump-schema` / `--migrations-dir`
> เป็น global flag วางก่อน `up`, Postgres ไม่มี TLS ต้องใส่ `?sslmode=disable`; LINE push รับ `X-Line-Retry-Key` (UUID)
> ส่งซ้ำได้ 409 (โค้ดถือว่าส่งแล้ว), reply ส่งได้ไม่เกิน 5 ข้อความ, loading animation 5–60 วิ.
> อายุ replyToken ไม่พบตัวเลขในเอกสาร LINE (บอกแค่ใช้ได้ครั้งเดียวและหมดอายุหลังช่วงหนึ่ง) — โค้ดใช้ `REPLY_TOKEN_TTL_S=50` แล้ว push แทนเมื่อใช้ไม่ได้

เป้าหมาย: ขึ้นทั้งระบบด้วย `docker compose` คุยกับ LINE OA ทดสอบได้ครบ flow (FAQ, ส่งต่อพนักงาน, ปิดเคส, kill switch, รูป)
โดย **ไม่ต่อ AI จริง** (`AI_API_URL` ว่าง -> `StubAiClient` ทวนข้อความกลับ)

## 1. สิ่งที่ต้องมี

1. เครื่อง Linux ที่มี Docker + Docker Compose v2 (`docker compose version` ต้องได้ v2.x)
2. โดเมน (เช่น `bot-test.example.com`) ที่ DNS ชี้มาที่เครื่องนี้ และเปิดพอร์ต 80 กับ 443 จากอินเทอร์เน็ต
   (Caddy ขอใบรับรอง Let's Encrypt เอง)
   - ไม่มีโดเมน (ทดสอบเท่านั้น): ใช้ Cloudflare quick tunnel ดูข้อ 4.6 — **ข้าม Caddy** (ไม่มีตัวกรอง route
     และไม่จำกัดขนาด body) ห้ามใช้ทางนี้กับ OA จริง
3. LINE Official Account **สำหรับทดสอบ** (ไม่ใช่ OA จริงของลูกค้า) + บัญชี LINE ที่เพิ่ม OA นี้เป็นเพื่อนไว้ทดสอบ

## 2. ตั้ง LINE Developers / LINE Official Account Manager

1. สร้าง Messaging API channel ของ OA ทดสอบ — เช็คหน้าจอจริง: ตอนนี้อาจต้องสร้าง OA ใน LINE Official Account Manager
   ก่อน แล้วเปิด Messaging API ที่ ตั้งค่า -> Messaging API แทนการสร้างใน LINE Developers Console ตรง ๆ
2. LINE Developers Console -> channel นั้น
   - แท็บ Basic settings: คัดลอก **Channel secret** -> `LINE_CHANNEL_SECRET`
   - แท็บ Messaging API: **Channel access token (long-lived)** กด Issue แล้วคัดลอก -> `LINE_CHANNEL_ACCESS_TOKEN`
3. แท็บ Messaging API -> Webhook settings
   - Webhook URL: `https://<DOMAIN>/webhook`
   - เปิด **Use webhook**
   - กด **Verify** ได้หลังขึ้นระบบแล้วเท่านั้น (ข้อ 5.2)
4. ปิดข้อความอัตโนมัติของ LINE เอง (ไม่งั้นลูกค้าได้ 2 ข้อความ): LINE Official Account Manager -> ตั้งค่าการตอบกลับ
   (Response settings) -> ปิด **ข้อความตอบกลับอัตโนมัติ (Auto-reply)** และ **ข้อความทักทาย (Greeting)**, เปิด **Webhook**
   — เช็คหน้าจอจริง (ชื่อเมนู/ที่อยู่ของปุ่ม; โหมด "แชท" จะเปิดไว้ก็ได้ถ้าจะให้พนักงานตอบในแชทระหว่างทดสอบ)

## 3. ตั้ง `.env`

```sh
cp .env.example .env
chmod 600 .env
```

ค่าที่ต้องเติมสำหรับรอบทดสอบนี้ (ที่เหลือปล่อยตาม `.env.example`):

```dotenv
LINE_CHANNEL_ACCESS_TOKEN=<channel access token จากข้อ 2.2>
LINE_CHANNEL_SECRET=<channel secret จากข้อ 2.2>

# ว่างไว้ตั้งใจ = โหมด stub (ไม่เรียก AI จริง) — AI_API_KEY / AI_TIMEOUT_S / AI_CONCURRENCY / TURN_DEADLINE_S ว่างได้
AI_API_URL=

# ต้องตรงกันทั้ง 4 บรรทัด (host = ชื่อ service "postgres"); รหัสผ่านอย่ามี $ และอักขระพิเศษต้อง URL-encode
DATABASE_URL=postgres://linebot:<รหัสผ่าน-db>@postgres:5432/linebot?sslmode=disable
POSTGRES_USER=linebot
POSTGRES_PASSWORD=<รหัสผ่าน-db>
POSTGRES_DB=linebot

REDIS_URL=redis://redis:6379

# MinIO ในเครื่อง: ACCESS/SECRET ต้องเท่ากับ ROOT_USER/ROOT_PASSWORD (รหัสผ่าน >= 8 ตัว)
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=linebot
MINIO_SECRET_KEY=<รหัสผ่าน-minio>
MINIO_ROOT_USER=linebot
MINIO_ROOT_PASSWORD=<รหัสผ่าน-minio>
MINIO_BUCKET=line-images
MINIO_SECURE=false

# ลิงก์ /close และ Caddy
PUBLIC_BASE_URL=https://<DOMAIN>
DOMAIN=<DOMAIN>

# ว่างไว้ = ถือว่าอยู่ในเวลาทำการเสมอ (ได้ข้อความส่งต่อแบบในเวลาทำการ)
CALLCENTER_HOURS=
KILL_SWITCH=false
```

- `<DOMAIN>` ไม่มี `https://` เช่น `bot-test.example.com`
- ขาดค่าบังคับ (LINE_*, DATABASE_URL, REDIS_URL) -> api/worker หยุดทันทีตอนเริ่ม ดูใน `docker compose logs api`

## 4. รัน

1. เอาโค้ด
   ```sh
   git clone <repo-url> line_prod && cd line_prod
   git checkout feat/python-impl
   ```
   (ชื่อโฟลเดอร์ = ชื่อ project ของ compose -> network ชื่อ `line_prod_default`)
2. `docker compose build`
3. `docker compose up -d`
4. `docker compose ps -a` — คาดว่า: `postgres`, `redis` = healthy; `migrate` = Exited (0); `api` = healthy;
   `worker`, `minio`, `caddy` = running
5. ดู log: `docker compose logs -f api worker` (log เป็น JSON หนึ่งบรรทัดต่อเหตุการณ์)
   - migrate: `docker compose logs migrate` ต้องเห็น dbmate รอ DB แล้ว apply `20261022000000_init.sql` และ
     `20261023000000_retention_indexes.sql` (รันซ้ำรอบหลังจะไม่มีอะไรให้ apply และยังจบด้วย 0)
   - worker ต้องเห็น `ai_stub_mode` (ยืนยันว่าเป็นโหมด stub) และ `worker_started`
6. (ทางเลือก ไม่มีโดเมน) Cloudflare quick tunnel — ทดสอบเท่านั้น ข้าม Caddy:
   ```sh
   docker compose up -d api worker          # ไม่ต้องเริ่ม caddy
   docker run --rm --network line_prod_default cloudflare/cloudflared tunnel --url http://api:8000
   ```
   ได้ URL `https://<สุ่ม>.trycloudflare.com` -> ใส่เป็น Webhook URL (`.../webhook`) และ `PUBLIC_BASE_URL`
   แล้ว `docker compose up -d api worker` อีกครั้งให้อ่าน `.env` ใหม่ (URL เปลี่ยนทุกครั้งที่รัน tunnel ใหม่)

## 5. เช็คลิสต์

คำสั่งผู้ดูแลรันใน container: `docker compose exec worker python -m app.worker.admin <คำสั่ง>`
(log JSON ของ app อาจพิมพ์ปนมาด้วย ผลของคำสั่งคือบรรทัดที่ขึ้นต้นตามที่เขียนไว้)

| # | ทำ | ผลที่คาด |
|---|---|---|
| 1 | `curl -i https://<DOMAIN>/health` | `200` `{"status":"ok"}` |
| 1b | `docker compose exec api python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/health/ready').read().decode())"` | `{"status":"ok","checks":{"postgres":true,"redis":true}}` (`/health/ready` ไม่เปิดผ่าน Caddy: จากข้างนอกได้ 404) |
| 2 | LINE Developers -> Webhook settings -> กด Verify | Success (api ตอบ 200; event ว่างไม่ทำอะไร) ถ้าได้ 401 ดูข้อ 8.1 |
| 3 | ส่ง `สวัสดี` | `สวัสดีค่ะ ต้องการให้ช่วยหาสินค้าอะไรคะ` (FAQ จาก `prompts/rules/faq.txt` ไม่เรียก AI) |
| 4 | ส่ง `ตู้เย็น` | `(โหมดทดสอบ ยังไม่ได้ต่อ AI API) ได้รับข้อความ: ตู้เย็น` (StubAiClient) |
| 5 | ส่ง `คุยกับพนักงาน` | `รับเรื่องแล้วค่ะ กำลังส่งต่อให้เจ้าหน้าที่ เจ้าหน้าที่จะตอบกลับในแชทนี้ค่ะ` และใน log (api หรือ worker แล้วแต่ใครทำก่อน): `docker compose logs api worker \| grep callcenter_handoff_started` เห็น `conversation_id`, `handoff_reason`, `has_close_url: true` (ไม่มีตัวลิงก์ — ตั้งใจ) |
| 6 | ส่งข้อความอะไรก็ได้ระหว่างนี้ เช่น `ตู้เย็น` | **เงียบ** (บันทึกแต่ไม่ตอบ; `ai_runs.route = 'silent'`) |
| 7 | `docker compose exec worker python -m app.worker.admin conversations --status human` | บรรทัด `conversation_id=<id> user_id=... status=human handoff_started_at=... handoff_reason=...` และ `conversations status=human count=1` |
| 8 | `docker compose exec worker python -m app.worker.admin close-link <id>` | พิมพ์ `https://<DOMAIN>/close?token=...` — **ลิงก์เป็นความลับ** (ใครมีลิงก์ปิดเคสได้ ใช้ได้ครั้งเดียว อายุ `CLOSE_TOKEN_TTL_HOURS`) ห้ามแปะในแชทกลุ่ม/ticket; ไม่ใช่ human -> พิมพ์ `close-link conversation_id=<id> ไม่สร้างลิงก์: ...` exit 1 |
| 9 | เปิดลิงก์ในเบราว์เซอร์ | หน้า `ลิงก์นี้ใช้ปิดเคส` + ปุ่ม `ปิดเคสและให้ลูกค้ากลับไปคุยกับ AI` (GET ยังไม่ปิดอะไร) |
| 10 | กดปุ่ม | หน้า `ปิดเคสแล้ว`; ลูกค้าได้ `กลับมาคุยกับผู้ช่วย AI แล้วค่ะ ต้องการหาสินค้าอะไรคะ` (ส่งแบบ push); log `callcenter_handoff_ended` end_reason `staff_closed`; กดลิงก์เดิมซ้ำ -> `ลิงก์ใช้ไม่ได้หรือถูกใช้ไปแล้ว` |
| 11 | ส่ง `คุยกับพนักงาน` อีกรอบ แล้วส่ง `คุยกับ AI` | ได้ข้อความส่งต่อ แล้ว `กลับมาคุยกับผู้ช่วย AI แล้วค่ะ ต้องการหาสินค้าอะไรคะ`; ส่ง `คุยกับ AI` ตอนเป็น bot อยู่แล้ว -> `ตอนนี้กำลังคุยกับผู้ช่วย AI อยู่แล้วค่ะ ต้องการหาสินค้าอะไรคะ` |
| 12 | `docker compose exec worker python -m app.worker.admin kill-switch on` แล้วส่ง `ตู้เย็น` | พิมพ์ `kill-switch effective=on env=off db=on`; ลูกค้า **ไม่ได้คำตอบ**; log `send.dropped` decision `dropped_kill_switch` |
| 13 | `... admin kill-switch off` แล้วส่ง `ตู้เย็น` | พิมพ์ `kill-switch effective=off env=off db=off`; ได้คำตอบโหมดทดสอบตามข้อ 4 (ข้อความที่ถูกทิ้งตอนเปิด ไม่ถูกส่งย้อนหลัง) |
| 14 | ส่งรูป | ตอบ `ต้องการให้ช่วยหาสินค้าอะไรคะ` (รูปยังไม่ส่งให้ AI — รอคำตอบเรื่อง AI API); รูปถูกเก็บ: ดูข้อ 5.1 |

`... admin kill-switch status` ดูสถานะได้ตลอด; ถ้า `env=on` (KILL_SWITCH=true ใน `.env`) ปิดด้วยคำสั่งนี้ไม่ได้

### 5.1 ตรวจในฐานข้อมูล / MinIO

เข้า psql (ตัวแปรขยายใน container ไม่ใช่บนเครื่อง host):

```sh
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

```sql
-- เส้นทางของแต่ละข้อความ (rule_reply / ai_answer / rule_escalate / silent / resumed ...)
SELECT id, conversation_id, route, reason, started_at FROM ai_runs ORDER BY id DESC LIMIT 20;
-- ข้อความขาออก: sent / dropped (drop_reason kill_switch, human) / failed
SELECT id, direction, message_type, status, drop_reason, created_at FROM messages ORDER BY id DESC LIMIT 20;
-- รูป: key = img/<user_id>/<raw_event_id>.jpg
SELECT id, message_id, storage_key, content_type, size_bytes FROM attachments ORDER BY id DESC LIMIT 5;
-- kill switch ใน DB
SELECT * FROM ops_flags;
```

ไฟล์รูปใน MinIO (ไม่ได้เปิดพอร์ต console ออกนอกเครื่อง): MinIO เก็บแต่ละ object เป็นโฟลเดอร์บนดิสก์
```sh
docker compose exec minio ls -R /data/line-images/img
```
ต้องเห็นโฟลเดอร์ `<raw_event_id>.jpg` — เช็คของจริง (รูปแบบการเก็บบนดิสก์ขึ้นกับเวอร์ชัน MinIO)

## 6. วัดเวลา

ใน psql เดียวกัน (คอลัมน์จาก `migrations/20261022000000_init.sql`):

```sql
-- ต่อขั้น (ai_steps.latency_ms) ย้อนหลัง 1 วัน
SELECT step, count(*) AS n,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
       max(latency_ms) AS max_ms
FROM ai_steps
WHERE latency_ms IS NOT NULL AND created_at > now() - interval '1 day'
GROUP BY step ORDER BY p95_ms DESC;

-- ทั้งรอบใน worker (ai_runs: started_at -> finished_at) แยกตาม route
SELECT route, count(*) AS n,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at) * 1000) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at) * 1000) AS p95_ms
FROM ai_runs
WHERE finished_at IS NOT NULL AND started_at > now() - interval '1 day'
GROUP BY route ORDER BY n DESC;

-- ลูกค้ากดส่ง (เวลาใน event ของ LINE) -> เราส่งคำตอบสำเร็จ
SELECT count(*) AS n,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY extract(epoch FROM m.sent_at - r.occurred_at) * 1000) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM m.sent_at - r.occurred_at) * 1000) AS p95_ms
FROM messages m JOIN line_raw_events r ON r.id = m.raw_event_id
WHERE m.direction = 'out' AND m.status = 'sent' AND m.created_at > now() - interval '1 day';
```

โหมด stub ไม่มีเวลาของ AI จริง (`call_ai` เกือบ 0) — ตัวเลขนี้คือเวลาของระบบเราเอง

## 7. ปิด / ล้าง

- `docker compose down` — หยุดและลบ container ข้อมูลยังอยู่ (volume)
- `docker compose down -v` — **ลบข้อมูลทั้งหมด** (Postgres, MinIO, ใบรับรองของ Caddy) ย้อนไม่ได้;
  ลบใบรับรองบ่อย ๆ จะโดน Let's Encrypt จำกัดจำนวน
- เลิกทดสอบ: ปิด Use webhook ใน LINE Developers และ revoke channel access token ที่ใช้

## 8. ปัญหาที่น่าจะเจอ

1. **Verify / webhook ได้ 401** — `LINE_CHANNEL_SECRET` ผิด (ใช้ access token แทน secret, มีช่องว่าง/ขึ้นบรรทัดเกิน,
   หรือเป็น secret ของ channel อื่น); log `webhook_bad_signature`. แก้ `.env` แล้ว `docker compose up -d api worker`
   (แค่ `restart` ไม่อ่าน env_file ใหม่)
2. **Let's Encrypt ไม่ผ่าน** (`docker compose logs caddy`) — DNS ยังไม่ชี้มาที่เครื่อง / พอร์ต 80 หรือ 443 ถูก firewall
   หรือ cloud security group ปิด / `DOMAIN` ว่างหรือมี `https://` / มีโปรแกรมอื่นจองพอร์ต 80/443
3. **migrate ไม่จบ (exit ไม่ใช่ 0) แล้ว api/worker ไม่เริ่ม** — `docker compose logs migrate`: `DATABASE_URL` ไม่ตรงกับ
   `POSTGRES_*`, host ไม่ใช่ `postgres`, ลืม `?sslmode=disable`, รหัสผ่านมีอักขระพิเศษที่ไม่ได้ encode.
   หมายเหตุ: `POSTGRES_*` มีผลแค่ตอนสร้าง volume ครั้งแรก — เปลี่ยนรหัสทีหลังต้อง `down -v` (ลบข้อมูล) หรือแก้ใน psql
4. **webhook ตอบ 200 แต่ไม่มีใครตอบลูกค้า** — worker ไม่หยิบงาน: `REDIS_URL` ของ api กับ worker ต้องชี้ Redis ตัวเดียวกัน
   (`redis://redis:6379`), ดู `docker compose logs worker` ว่ามี `worker_started` และไม่ crash; แถวค้าง
   `SELECT process_status, count(*) FROM line_raw_events GROUP BY 1;` (pending ค้าง -> cron requeue_pending ใส่คิวใหม่ทุก 1 นาที)
5. **replyToken หมดอายุ -> ส่งแบบ push** — เราเชื่อ replyToken ไม่เกิน `REPLY_TOKEN_TTL_S` (50 วิ) เกินนั้นใช้ push
   (log `send.sent` via `push`); ข้อความ RESUMED หลังกดปิดเคสเป็น push เสมอ. push นับโควตาข้อความรายเดือนของ OA
   (แผนฟรีมีจำกัด — เช็คหน้าจอจริงที่ LINE Official Account Manager) ส่วน reply ไม่นับ
6. **api ไม่ healthy / ขึ้น ๆ ลง ๆ** — `docker compose logs api` มักเป็นค่าบังคับใน `.env` ขาด หรือ Postgres/Redis ต่อไม่ได้
