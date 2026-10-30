# migrations/

schema ของ Postgres 16 เป็นไฟล์ `.sql` รันด้วย dbmate เก็บเวอร์ชันไว้กับโค้ด

สถานะ: มี `20261022000000_init.sql` (ร่าง ยังไม่เคยรันกับ Postgres จริง)

- คำสั่ง: `dbmate up` / `dbmate rollback` (อ่าน `DATABASE_URL`)
- ไฟล์: `YYYYMMDDHHMMSS_ชื่อ.sql` มีส่วน `-- migrate:up` และ `-- migrate:down`
- มีแต่ `app/repositories/` ที่เขียน SQL ในโค้ด แก้ schema ที่นี่แล้วตามแก้ที่นั่น
- `20261024000000_procrastinate_schema.sql` = schema ของคิว procrastinate 3.10.0 (D-010) คัดลอกจากแพ็กเกจ ห้ามแก้มือ;
  ตาราง `procrastinate_*` เป็นของไลบรารี — อัปเกรด procrastinate ทำตามหัวไฟล์ (เพิ่ม migration ใหม่ ไม่แก้ไฟล์นี้)

## ต้นทาง

`docs/Line_ai_bot.md` หัวข้อ ฐานข้อมูล อ้างถึง `line_chat_schema_v2.sql` (ตาราง `line_raw_events`, `line_users`, `line_consents`, `conversations`, `conversation_state`, `messages`, `attachments`, `ai_runs`, `ai_steps`, `feedback`) — **ไฟล์นี้ยังไม่อยู่ใน repo** — migration แรกจึงเป็นร่างจากแบบหลัก ต้องเทียบเมื่อได้ไฟล์จริง

## คอลัมน์ที่ต้องเพิ่มจากฉบับในไฟล์ (ตาราง `conversations`)

| คอลัมน์ | ค่า | ที่มา |
|---|---|---|
| `handoff_started_at` | timestamptz | การส่งต่อพนักงาน (นับ 6 ชม. จากตรงนี้) |
| `handoff_ended_at` | timestamptz | การส่งต่อพนักงาน |
| `handoff_end_reason` | user_resumed / timeout / staff_closed | การส่งต่อพนักงาน |
| `outcome` | resolved / handed_off / abandoned / unknown | นิยามความสำเร็จ (ข้อเสนอ) |
| `outcome_source` | feedback / heuristic / staff_label / reviewed | นิยามความสำเร็จ (ข้อเสนอ) |
| `staff_label` | ส่งต่อถูกต้อง / ส่งต่อเกิน / ลูกค้าขอเอง | นิยามความสำเร็จ (ข้อเสนอ) |

ยังไม่ตัดสิน: `handoff_deadline` (ถ้าให้พนักงานต่อเวลาเกิน 6 ชม.)
