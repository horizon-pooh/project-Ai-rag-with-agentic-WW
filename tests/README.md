# tests/

ชุดทดสอบของแบบใหม่ (Python) — โฟลเดอร์ `test/` เป็นของแบบเก่า (JS) ห้ามแก้

สถานะ: มีเทสต์หน่วยรอบแรก (ใช้ fake ไม่ต่อ Postgres/Redis/LINE/AI จริง)

| โฟลเดอร์ | ทดสอบอะไร |
|---|---|
| `.` (ราก) | `test_config.py`, `test_fakes.py`, `test_smoke.py` + `fakes.py`, `infra_fakes.py` |
| `pipeline/` | ทีละ step, orchestrator, handoff, ไฟล์กฎ |
| `services/` | client ภายนอก จำลอง HTTP ด้วย respx, Redis ด้วย fake |
| `api/` | webhook, /close, /health ผ่าน create_app + fake |
| `worker/` | tasks, locks, cron, settings/deps |
| `privacy/` | รูปแบบ redact |
| `repositories/` | SQL: static เสมอ, integration ข้ามถ้าไม่ตั้ง `TEST_DATABASE_URL` |
| `scenarios/` | regression 30 บทสนทนา (ตอนนี้สังเคราะห์ 3) |

- เครื่องมือ: pytest, pytest-asyncio (`asyncio_mode = "auto"`), respx
- รัน: `uv run python -m pytest` (เครื่องที่ไม่บล็อก exe launcher ใช้ `uv run pytest` ได้) ตั้งค่าใน `pyproject.toml`
- ทุกคำในไฟล์กฎ (`prompts/rules/`) และทุกรูปแบบ redact ต้องมีเคสทดสอบภาษาไทยจริง
- เขียนทีละส่วนบางๆ: webhook -> ตอบข้อความตายตัว แล้ววัดเวลา ค่อยเพิ่มขั้นถัดไป
