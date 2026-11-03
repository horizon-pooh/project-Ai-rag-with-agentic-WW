-- index สำหรับงาน retention / anonymize (app/repositories/messages.py, ai_runs.py)
-- เหตุผล: คิวรีเหล่านี้กรอง created_at < X + เงื่อนไข "ยังมีค่า" แล้ว ORDER BY created_at, id LIMIT n
-- ไม่มี index จะ scan ทั้งตารางทุกรอบ; partial index เก็บเฉพาะแถวที่ยังต้องจัดการ
-- (แถวที่ anonymize/ลบแล้วหลุดออกจาก index เอง ทำให้ index เล็กลงเรื่อย ๆ)
-- เงื่อนไข WHERE ของ index ต้องตรงกับคิวรีทุกตัวอักษร ถ้าแก้คิวรีต้องตามแก้ที่นี่

-- migrate:up
CREATE INDEX messages_retention_idx ON messages (created_at, id)
    WHERE text_redacted IS NOT NULL OR payload IS NOT NULL;

CREATE INDEX ai_steps_retention_idx ON ai_steps (created_at, id)
    WHERE input IS NOT NULL OR output IS NOT NULL
       OR state_before IS NOT NULL OR state_after IS NOT NULL;

CREATE INDEX attachments_retention_idx ON attachments (created_at, id)
    WHERE deleted_at IS NULL;

-- migrate:down
DROP INDEX IF EXISTS attachments_retention_idx;
DROP INDEX IF EXISTS ai_steps_retention_idx;
DROP INDEX IF EXISTS messages_retention_idx;
