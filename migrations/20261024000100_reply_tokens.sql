-- replyToken ของ LINE ย้ายจาก Redis มาอยู่ Postgres (docs/decisions.md D-010: ไม่มี Redis แล้ว)
--
-- - หนึ่งแถวต่อ raw event; ใช้ได้ครั้งเดียว: take = DELETE ... RETURNING (app/repositories/reply_tokens.py)
-- - expires_at = now() + REPLY_TOKEN_TTL_S ตอน put; เกินแล้ว take ได้ None -> sendGuard ใช้ push แทน
-- - เป็นความลับอายุสั้น: ห้ามลง log; line_raw_events.payload ยังห้ามมี replyToken
--   (CHECK line_raw_events_no_reply_token ใน 20261022000000_init.sql คงเดิม)
-- - แถวที่หมดอายุแต่ไม่ถูก take: cron retention ลบทิ้ง; ลบ raw event (retention 90 วัน / PDPA) -> CASCADE ลบตาม

-- migrate:up
CREATE TABLE reply_tokens (
    raw_event_id  bigint      PRIMARY KEY REFERENCES line_raw_events (id) ON DELETE CASCADE,
    token         text        NOT NULL,
    expires_at    timestamptz NOT NULL
);
-- cron retention: ลบแถวที่หมดอายุ
CREATE INDEX reply_tokens_expires_idx ON reply_tokens (expires_at);

-- migrate:down
DROP TABLE IF EXISTS reply_tokens;
