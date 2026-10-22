# tests/scenarios/

ชุด regression 30 บทสนทนา (docs/Line_ai_bot.md หัวข้อ 10 การสังเกตและทดสอบ, 11.2)

สถานะ: มีตัวรันแล้ว + ตัวอย่างสังเคราะห์ 3 บทสนทนา — **รอข้อมูลจริง** (ยังไม่มีแชทจริงสักเคส)

| ข้อตกลง | รายละเอียด |
|---|---|
| จำนวน | 30 บทสนทนา จากแชทจริง (redact แล้ว) — ตอนนี้ 0 / 30 (รอข้อมูลจริง) |
| ควรส่งต่อ | ราว 10 เคส (ร้องเรียน, เคลม, ขอคน, AI ตอบไม่ได้) — ตอนนี้ 0 / 10 จากแชทจริง |
| ป้าย | ติดป้ายเส้นทางที่ถูก (ตอบเอง / ถามกลับ / ส่งต่อ) **ก่อนรัน** ห้ามแก้ป้ายให้ตรงผล |
| เมื่อไหร่รัน | ทุกครั้งที่แก้ prompt, ไฟล์กฎ หรือเปลี่ยน AI API |
| ผลที่ดู | เส้นทางตรงป้าย, ไม่ส่งต่อขาด (ควรส่งแต่ตอบไป — ต้องเป็น 0), ส่งต่อเกิน |

## ไฟล์

- `test_scenarios.py` — ตัวรัน: หนึ่งไฟล์เคส = หนึ่งเทสต์ + `test_summary` (ตารางสรุป และ fail ถ้าไม่ส่งต่อขาด)
- `cases/*.toml` — หนึ่งไฟล์ต่อบทสนทนา
  - `synthetic_001_fridge_refine.toml` — ตู้เย็น → เขียว → 2 ประตู (self_answer)
  - `synthetic_002_vague_clarify_escalates.toml` — ถามกลับเกิน CLARIFY_MAX แล้วส่งต่อ (handoff)
  - `synthetic_003_damaged_claim_rule.toml` — "สินค้าชำรุด อยากเคลม" ส่งต่อด้วยกฎ ไม่เรียก AI (handoff)

ตัวรันใช้ไฟล์กฎจริงใน `prompts/rules/` และ fake ทั้งหมด (tests/fakes.py) ไม่ต่อ LINE / AI API จริง
ผล AI เขียนไว้ในไฟล์เคส (scripted) — ชุดนี้จึงทดสอบ "โค้ดตัดสินเส้นทางถูกไหมเมื่อ AI ตอบแบบนี้"
ไม่ได้ทดสอบคุณภาพคำตอบของ AI API จริง (รอคำตอบ: AI API / สภาพแวดล้อมทดสอบของ API)

## รูปแบบไฟล์ (TOML)

```toml
# ตัวอย่างสังเคราะห์ ไม่ใช่แชทจริง   <- เคส synthetic ต้องมีบรรทัดนี้
id = "real-007"                    # ไม่ซ้ำกัน
source = "real"                    # "synthetic" | "real"
description = "ลูกค้าถามตู้เย็น แล้วขอเคลม"
expected_path = "handoff"          # "self_answer" | "clarify" | "handoff"

[[turns]]
text = "หาตู้เย็น 2 ประตู"           # ข้อความลูกค้า (redact แล้ว) หรือ postback = "action=..." อย่างใดอย่างหนึ่ง
advance_minutes = 1                # (ไม่บังคับ) เลื่อนนาฬิกาก่อนรอบนี้
route = "ai_answer"                # ค่า TurnRoute ที่คาด (app/pipeline/orchestrator.py)
expect_status = "bot"              # (ไม่บังคับ) สถานะบทสนทนาหลังรอบนี้: "bot" | "human"
expect_sent = ["sent"]             # (ไม่บังคับ) ค่า SendStatus ของทุกการส่งในรอบนี้
[turns.ai]                         # (ไม่บังคับ) ผลที่ AI ตอบในรอบนี้
kind = "products"                  # products | answer | clarify | cannot_answer
intro = "แนะนำตู้เย็นค่ะ"
products = [{ sku = "RF-100", name = "ตู้เย็น 2 ประตู", price = "12990", url = "https://shop.example/p/rf-100" }]
clarify_question = ""              # สำหรับ kind = "clarify"
escalate_hint = false
state_out = { category = "ตู้เย็น" }

[[turns]]
text = "ของมาชำรุด ขอเคลม"
route = "rule_escalate"
expect_status = "human"
# ไม่มี [turns.ai] / ai_error -> ถ้า AI ถูกเรียกในรอบนี้ เทสต์ fail
```

- แทน `[turns.ai]` ด้วย `ai_error = "unavailable"` หรือ `"bad_response"` เพื่อจำลอง AI ล่ม / ตอบผิดรูป
- ฟิลด์ที่ไม่รู้จัก, ไม่มี text/postback, หรือ `expected_path` ไม่ตรงกับ route (handoff ⇔ มีรอบที่ route เป็น
  `user_escalate` / `rule_escalate` / `ai_escalate` / `ai_down_escalate` / `validate_escalate`) -> error ตอนอ่านไฟล์

## เพิ่มเคสจริง

1. เอาแชทจริงหนึ่งบทสนทนา **redact ก่อน** (เบอร์โทร, อีเมล, ชื่อ, ที่อยู่, เลขบัญชี/บัตร, เลขพัสดุ —
   แนวเดียวกับ `app/privacy/redact.py`) ห้าม commit ข้อความดิบหรือ LINE userId
2. ติดป้าย `expected_path` และ `route` ทุกรอบ **ก่อนรัน** (คนที่รู้งานหน้าร้านเป็นคนตัดสิน ไม่ใช่ดูจากผลโค้ด)
3. ใส่ผล AI ที่ API ตอบจริงในแชทนั้น (ถ้ามี log) ลง `[turns.ai]` ไม่งั้นเขียนผลที่สมเหตุสมผลแล้วจดใน `description`
4. ตั้งชื่อ `cases/real_NNN_<เรื่องสั้นๆ>.toml`, `source = "real"`
5. รันแล้วดูตาราง — ถ้าเส้นทางไม่ตรงป้าย แก้โค้ด/prompt/กฎ ไม่ใช่แก้ป้าย

## วิธีรัน

```
uv run python -m pytest tests/scenarios -q -s
```

`-s` แสดงตารางสรุป (ทั้งหมด / ผ่าน / ควรส่งต่อ / ไม่ส่งต่อขาด / ส่งต่อเกิน)
บน Windows ถ้าภาษาไทยในตารางเพี้ยน ตั้ง `PYTHONIOENCODING=utf-8` ก่อนรัน

## ยังขาด (รอข้อมูลจริง)

- 30 บทสนทนาจากแชทจริง โดยราว 10 เคสควรส่งต่อ — ตอนนี้มีแค่ตัวอย่างสังเคราะห์ 3 เคส
- ตัวอย่างสังเคราะห์ไม่นับเป็นส่วนของ 30 เคส (เก็บไว้เป็นตัวอย่างรูปแบบและเทสต์ของตัวรัน)

แนวคิดจาก: `test/scenarios/README.md` (แบบเก่า), `docs/Line_ai_bot.md` หัวข้อ หลักการเขียนโปรแกรม
