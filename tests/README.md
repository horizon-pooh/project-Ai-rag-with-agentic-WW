# tests/

ชุดทดสอบของแบบใหม่ (Python) — โฟลเดอร์ `test/` เป็นของแบบเก่า (JS) ห้ามแก้

สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)

| โฟลเดอร์ | ทดสอบอะไร |
|---|---|
| `pipeline/` | ทีละ step ไม่ต่อ LINE จริง (เช่น `test_handoff_check.py`) |
| `services/` | client ภายนอก จำลอง HTTP ด้วย respx (เช่น `test_ai_client.py`) |
| `scenarios/` | regression 30 บทสนทนา |

- เครื่องมือ: pytest, pytest-asyncio (`asyncio_mode = "auto"`), respx
- รัน: `pytest` (ตั้งค่าใน `pyproject.toml`)
- ทุกคำในไฟล์กฎ (`prompts/rules/`) และทุกรูปแบบ redact ต้องมีเคสทดสอบภาษาไทยจริง
- เขียนทีละส่วนบางๆ: webhook -> ตอบข้อความตายตัว แล้ววัดเวลา ค่อยเพิ่มขั้นถัดไป
