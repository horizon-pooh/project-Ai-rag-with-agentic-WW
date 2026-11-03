-- ร่าง: ไม่มี line_chat_schema_v2.sql ใน repo ต้องเทียบกับไฟล์จริงเมื่อได้มา
--
-- schema แรกของแบบใหม่ (Postgres 16) เขียนจาก docs/Line_ai_bot.md หัวข้อ 4, 5, 7, 8 และ docs/PLAN.md
-- หลักที่ใช้:
--  - PK เป็น bigint identity, เวลาเป็น timestamptz ทั้งหมด (ความละเอียดไมโครวินาที เท่ากับ datetime ของ Python
--    จึงเทียบ handoff_started_at = $3 ใน end_handoff ได้ตรง)
--  - ค่า enum ใช้ CHECK (เพิ่มค่าใหม่ = migration ใหม่ ไม่ต้องยุ่งกับ type)
--  - ลบตาม PDPA ที่ line_users ที่เดียว: ทุกตารางที่เป็นของ user ต่อ FK ลงมาด้วย ON DELETE CASCADE
--    ข้อยกเว้น: line_raw_events ไม่มี FK ไป line_users (บันทึกตอน webhook ก่อนมี user) ต้องลบตาม line_user_id แยก
--    และไฟล์ใน MinIO ต้องลบแยก
--  - FK ที่ชี้ line_raw_events ใช้ ON DELETE SET NULL เพราะ raw เก็บ 90 วัน แต่ข้อความ/trace เก็บ 12 เดือน
--  - มีแต่ app/repositories/ ที่เขียน SQL ใส่ตารางเหล่านี้

-- migrate:up

-- ผู้ใช้ LINE (ต้นทางของการลบตาม PDPA)
CREATE TABLE line_users (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    line_user_id  text        NOT NULL UNIQUE,
    display_name  text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);

-- การยินยอม (เก็บทุกครั้งที่เปลี่ยน แถวล่าสุดของ consent_type คือค่าปัจจุบัน)
CREATE TABLE line_consents (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       bigint      NOT NULL REFERENCES line_users (id) ON DELETE CASCADE,
    consent_type  text        NOT NULL,
    granted       boolean     NOT NULL,
    version       text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX line_consents_user_type_idx ON line_consents (user_id, consent_type, created_at DESC);

-- เหตุการณ์ดิบจาก webhook (บันทึกก่อนตอบ 200) — payload ตัด replyToken ออกแล้ว (replyToken อยู่ Redis)
CREATE TABLE line_raw_events (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    webhook_event_id  text        NOT NULL UNIQUE,
    line_user_id      text,
    event_type        text        NOT NULL,
    payload           jsonb       NOT NULL,
    occurred_at       timestamptz NOT NULL,
    received_at       timestamptz NOT NULL DEFAULT now(),
    process_status    text        NOT NULL DEFAULT 'pending'
        CHECK (process_status IN ('pending', 'processing', 'done', 'failed', 'skipped')),
    claimed_at        timestamptz,
    processed_at      timestamptz,
    attempts          integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error             text,
    CONSTRAINT line_raw_events_no_reply_token CHECK (NOT (payload ? 'replyToken'))
);
-- event ที่ยังไม่เสร็จของ user หนึ่งคน เรียงตามเวลา (Worker ทำเก่าสุดก่อน)
CREATE INDEX line_raw_events_user_open_idx
    ON line_raw_events (line_user_id, occurred_at, id)
    WHERE process_status IN ('pending', 'processing');
-- งานเก็บกวาด: pending ค้างนาน (นับจาก received_at) และ processing ค้างนาน (นับจาก claimed_at)
CREATE INDEX line_raw_events_stale_pending_idx
    ON line_raw_events (received_at)
    WHERE process_status = 'pending';
CREATE INDEX line_raw_events_stale_processing_idx
    ON line_raw_events (claimed_at)
    WHERE process_status = 'processing';
-- retention 90 วัน
CREATE INDEX line_raw_events_received_idx ON line_raw_events (received_at);

-- บทสนทนา: status bot/human เป็นของระบบเรา (D-007)
CREATE TABLE conversations (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id               bigint      NOT NULL REFERENCES line_users (id) ON DELETE CASCADE,
    status                text        NOT NULL DEFAULT 'bot' CHECK (status IN ('bot', 'human')),
    opened_at             timestamptz NOT NULL DEFAULT now(),
    last_user_message_at  timestamptz,
    closed_at             timestamptz,
    handoff_started_at    timestamptz,
    handoff_reason        text,
    handoff_ended_at      timestamptz,
    handoff_end_reason    text
        CHECK (handoff_end_reason IN ('user_resumed', 'timeout', 'staff_closed')),
    outcome               text        NOT NULL DEFAULT 'unknown'
        CHECK (outcome IN ('resolved', 'handed_off', 'abandoned', 'unknown')),
    outcome_source        text
        CHECK (outcome_source IN ('feedback', 'heuristic', 'staff_label', 'reviewed')),
    staff_label           text
        CHECK (staff_label IN ('correct', 'over_escalated', 'user_requested')),
    -- นับ 6 ชม. จาก handoff_started_at จึงต้องมีค่าเสมอเมื่อเป็น human
    CONSTRAINT conversations_human_has_started CHECK (status <> 'human' OR handoff_started_at IS NOT NULL),
    -- human ห้ามปิด/เปิดใหม่ทับ (ปิดได้เฉพาะ bot ที่เงียบเกิน STATE_TTL)
    CONSTRAINT conversations_closed_is_bot CHECK (closed_at IS NULL OR status = 'bot')
);
-- เปิดได้ไม่เกินหนึ่ง conversation ต่อ user
CREATE UNIQUE INDEX conversations_one_open_per_user
    ON conversations (user_id)
    WHERE closed_at IS NULL;
-- cron handoff_timeout
CREATE INDEX conversations_handoff_started_idx
    ON conversations (handoff_started_at)
    WHERE status = 'human';

-- ความจำระยะสั้น: state ส่งให้ AI, counters ไม่ส่ง AI
CREATE TABLE conversation_state (
    conversation_id  bigint      PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    state            jsonb,
    -- clarify_streak, cannot_answer_streak, validate_fail_streak, last_products (ไม่ส่ง AI)
    counters         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL
);

-- ข้อความขาเข้า/ขาออก
-- ขาเข้า: status เป็น NULL, dedupe_key = 'in:<raw_event_id>' (บันทึกซ้ำตอน retry ไม่ได้)
-- ขาออก: message_type เก็บ purpose ของ OutboundMessage, payload = LINE message objects,
--        status บันทึกก่อนส่ง (pending) กันส่งซ้ำ
CREATE TABLE messages (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id  bigint      NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    raw_event_id     bigint      REFERENCES line_raw_events (id) ON DELETE SET NULL,
    direction        text        NOT NULL CHECK (direction IN ('in', 'out')),
    message_type     text        NOT NULL,
    text_redacted    text,
    payload          jsonb,
    status           text        CHECK (status IN ('pending', 'sent', 'dropped', 'failed')),
    drop_reason      text,
    dedupe_key       text        UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    sent_at          timestamptz,
    CONSTRAINT messages_out_has_status CHECK (direction = 'in' OR status IS NOT NULL),
    CONSTRAINT messages_in_has_no_status CHECK (direction = 'out' OR status IS NULL)
);
CREATE INDEX messages_conversation_created_idx ON messages (conversation_id, created_at);

-- รูป/ไฟล์ที่เก็บใน MinIO (เก็บแค่ key; ลบไฟล์จริงแยก)
CREATE TABLE attachments (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id    bigint      NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    storage_key   text        NOT NULL UNIQUE,
    content_type  text        NOT NULL,
    size_bytes    bigint      NOT NULL CHECK (size_bytes >= 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);
CREATE INDEX attachments_message_idx ON attachments (message_id);

-- หนึ่งรอบต่อหนึ่ง event (retry ใช้แถวเดิม)
CREATE TABLE ai_runs (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_event_id     bigint      UNIQUE REFERENCES line_raw_events (id) ON DELETE SET NULL,
    conversation_id  bigint      NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    route            text,
    reason           text,
    -- ช่วงค่าขึ้นกับ AI API — รอคำตอบ จึงยังไม่ใส่ CHECK
    confidence       numeric
);
CREATE INDEX ai_runs_conversation_idx ON ai_runs (conversation_id);

-- trace ทีละขั้น (orchestrator บันทึก) — ไม่มี UNIQUE (ai_run_id, seq) เพราะ retry ทำซ้ำ seq เดิมได้
CREATE TABLE ai_steps (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ai_run_id     bigint      NOT NULL REFERENCES ai_runs (id) ON DELETE CASCADE,
    raw_event_id  bigint      REFERENCES line_raw_events (id) ON DELETE SET NULL,
    seq           integer     NOT NULL,
    step          text        NOT NULL,
    input         jsonb,
    output        jsonb,
    state_before  jsonb,
    state_after   jsonb,
    latency_ms    integer     CHECK (latency_ms >= 0),
    attempts      integer     NOT NULL DEFAULT 1 CHECK (attempts >= 0),
    error_class   text,
    cost          numeric,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ai_steps_run_seq_idx ON ai_steps (ai_run_id, seq);

-- สัญญาณแทนความสำเร็จ (หัวข้อ 7)
CREATE TABLE feedback (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id  bigint      NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    ai_run_id        bigint      REFERENCES ai_runs (id) ON DELETE SET NULL,
    kind             text        NOT NULL
        CHECK (kind IN ('thumb_up', 'thumb_down', 'card_click', 'review_label')),
    value            text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX feedback_conversation_idx ON feedback (conversation_id);

-- ลิงก์ /close ใช้ครั้งเดียว: เก็บแค่ hash ของ token, ผูกรอบส่งต่อด้วย handoff_started_at
CREATE TABLE close_tokens (
    token_hash          text        PRIMARY KEY,
    conversation_id     bigint      NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    handoff_started_at  timestamptz NOT NULL,
    expires_at          timestamptz NOT NULL,
    used_at             timestamptz
);
CREATE INDEX close_tokens_conversation_idx ON close_tokens (conversation_id);

-- ค่าตั้งที่เปลี่ยนได้ตอนรัน เช่น kill switch (เปิด/ปิดไม่ต้อง deploy)
CREATE TABLE ops_flags (
    key         text        PRIMARY KEY,
    value       text        NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- migrate:down

DROP TABLE IF EXISTS ops_flags;
DROP TABLE IF EXISTS close_tokens;
DROP TABLE IF EXISTS feedback;
DROP TABLE IF EXISTS ai_steps;
DROP TABLE IF EXISTS ai_runs;
DROP TABLE IF EXISTS attachments;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS conversation_state;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS line_raw_events;
DROP TABLE IF EXISTS line_consents;
DROP TABLE IF EXISTS line_users;
