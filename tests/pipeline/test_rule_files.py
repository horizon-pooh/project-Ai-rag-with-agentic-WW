"""tests/pipeline/test_rule_files.py — ไฟล์กฎจริงใน prompts/rules/ (ทุกรายการต้องมีเคสไทยจริง)

วิธีจับตาม docs/PLAN.md (เขียนซ้ำในเทสต์นี้ ไม่ import rules.py ที่งานอื่นเขียน):
 - escalate.txt: มีอยู่ในข้อความ (substring) หลัง normalize
 - faq.txt: ตรงทั้งข้อความหลัง normalize (pattern<TAB>คำตอบ)
 - resume_ai.txt: ตรงทั้งข้อความหลัง normalize

ทุกรายการต้องมีอย่างน้อยหนึ่งเคสที่ต้องจับ และหนึ่งเคสที่ต้องไม่จับ (test_every_entry_has_cases)
เพิ่มคำในไฟล์โดยไม่เพิ่มเคส -> เทสต์พัง
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.text import load_faq, load_rule_lines, normalize

RULES_DIR = Path(__file__).resolve().parents[2] / "prompts" / "rules"
ESCALATE = load_rule_lines(RULES_DIR / "escalate.txt")
FAQ = load_faq(RULES_DIR / "faq.txt")
RESUME = load_rule_lines(RULES_DIR / "resume_ai.txt")

GREETING = "สวัสดีค่ะ ต้องการให้ช่วยหาสินค้าอะไรคะ"
THANKS = "ยินดีค่ะ หากต้องการหาสินค้าเพิ่มเติม พิมพ์บอกได้เลยค่ะ"


def hits(rule_file: str, message: str) -> set[str]:
    """รายการในไฟล์ที่ข้อความนี้จับได้ ตามวิธีจับที่ตัดสินแล้ว"""
    msg = normalize(message)
    if rule_file == "escalate":
        return {e for e in ESCALATE if e in msg}
    if rule_file == "faq":
        return {msg} if msg in FAQ else set()
    if rule_file == "resume_ai":
        return {msg} if msg in RESUME else set()
    raise AssertionError(rule_file)


def entries(rule_file: str) -> set[str]:
    return {"escalate": set(ESCALATE), "faq": set(FAQ), "resume_ai": set(RESUME)}[rule_file]


# (ไฟล์, ข้อความลูกค้า, รายการที่ต้องจับ)
MUST_MATCH: list[tuple[str, str, str]] = [
    # escalate
    ("escalate", "จะร้องเรียนเรื่องพนักงานส่งของค่ะ", "ร้องเรียน"),
    ("escalate", "ขอร้อง เรียน ผู้จัดการด้วย", "ร้องเรียน"),  # ช่องว่างถูกลบตอน normalize
    ("escalate", "ตู้เย็นเคลมประกันได้ไหม", "เคลม"),
    ("escalate", "ทีวีจอเป็นเส้น อยากเคลมค่ะ", "เคลม"),
    ("escalate", "ขอคุยกับพนักงานหน่อยค่ะ", "คุยกับพนักงาน"),
    ("escalate", "ขอ คุย กับ พนักงาน", "คุยกับพนักงาน"),
    # ข้อจำกัดที่ยอมรับ: substring ไม่รู้จักคำปฏิเสธ ส่งต่อพลาดแค่เสียเวลาพนักงาน
    ("escalate", "ไม่ต้องคุยกับพนักงานค่ะ", "คุยกับพนักงาน"),
    ("escalate", "ขอคุยกับเจ้าหน้าที่ได้ไหมคะ", "คุยกับเจ้าหน้าที่"),
    ("escalate", "ขอคุยกับแอดมินตัวจริงค่ะ", "คุยกับแอดมิน"),
    ("escalate", "อยากติดต่อพนักงานเรื่องผ่อนบัตร", "ติดต่อพนักงาน"),
    ("escalate", "ติดต่อเจ้าหน้าที่ยังไงคะ", "ติดต่อเจ้าหน้าที่"),
    ("escalate", "บอทไม่เข้าใจ ขอคนจริงค่ะ", "ขอคนจริง"),
    ("escalate", "คุยกับบอทไม่รู้เรื่อง ขอคนจริง", "ขอคนจริง"),
    ("escalate", "ได้รับสินค้าชำรุดค่ะ กล่องบุบ", "สินค้าชำรุด"),
    # faq
    ("faq", "สวัสดี", "สวัสดี"),
    ("faq", "สวัสดี ค่ะ", "สวัสดีค่ะ"),
    ("faq", "สวัสดีครับ", "สวัสดีครับ"),
    ("faq", "Hello", "hello"),
    ("faq", "Hi ", "hi"),
    ("faq", "ขอบคุณ", "ขอบคุณ"),
    ("faq", "ขอบคุณ​ค่ะ", "ขอบคุณค่ะ"),  # มี zero-width คั่น
    ("faq", "ขอบคุณครับ", "ขอบคุณครับ"),
    ("faq", "Thank you", "thankyou"),
    ("faq", "THANKS", "thanks"),
    # resume_ai
    ("resume_ai", "คุยกับ AI", "คุยกับai"),
    ("resume_ai", "คุย กับ Ai", "คุยกับai"),
    ("resume_ai", "กลับไปคุยกับ AI", "กลับไปคุยกับai"),
    ("resume_ai", "คุยกับบอท", "คุยกับบอท"),
    ("resume_ai", "คุยกับ บอท", "คุยกับบอท"),
]

# (ไฟล์, ข้อความลูกค้า, รายการที่เคสนี้ตั้งใจทดสอบว่าไม่ชน) — ต้องไม่จับรายการใดในไฟล์เลย
MUST_NOT_MATCH: list[tuple[str, str, str]] = [
    # escalate
    ("escalate", "ขอเรียนสอบถามราคาตู้เย็นค่ะ", "ร้องเรียน"),
    ("escalate", "ตู้เย็นรุ่นนี้เคลือบสารกันแบคทีเรียไหม", "เคลม"),
    ("escalate", "มีเคลียร์สต็อกลดราคาไหมคะ", "เคลม"),
    ("escalate", "พนักงานติดตั้งมากี่โมงคะ", "คุยกับพนักงาน"),
    ("escalate", "เจ้าหน้าที่คุยเก่งมากค่ะ", "คุยกับเจ้าหน้าที่"),
    ("escalate", "แอดมินคะ ตู้เย็นรุ่นนี้มีสีอื่นไหม", "คุยกับแอดมิน"),
    ("escalate", "พนักงานจะติดต่อกลับเมื่อไหร่คะ", "ติดต่อพนักงาน"),
    ("escalate", "เจ้าหน้าที่ติดต่อมาแล้วค่ะ ขอบคุณ", "ติดต่อเจ้าหน้าที่"),
    ("escalate", "ของจริงสีเหมือนในรูปไหมคะ", "ขอคนจริง"),
    ("escalate", "ขอรูปจริงหน่อยค่ะ", "ขอคนจริง"),
    ("escalate", "สินค้าเสียงดังไหมคะ", "สินค้าชำรุด"),
    ("escalate", "ของเสียบปลั๊กพ่วงมีไหม", "สินค้าชำรุด"),
    ("escalate", "สินค้าชิ้นนี้รับประกันกี่ปีคะ", "สินค้าชำรุด"),
    # faq: ต้องตรงทั้งข้อความ มีคำถามต่อท้าย -> ส่ง AI
    ("faq", "สวัสดีปีใหม่ มีโปรไหม", "สวัสดี"),
    ("faq", "สวัสดีค่ะ อยากได้ตู้เย็น 2 ประตู", "สวัสดีค่ะ"),
    ("faq", "สวัสดีครับ มีแอร์ 12000 BTU ไหม", "สวัสดีครับ"),
    ("faq", "hello มีทีวี 55 นิ้วไหม", "hello"),
    ("faq", "hi-fi speaker มีไหม", "hi"),
    ("faq", "ขอบคุณที่แนะนำ ขอดูรุ่นอื่นด้วย", "ขอบคุณ"),
    ("faq", "ขอบคุณค่ะ เอารุ่นนี้", "ขอบคุณค่ะ"),
    ("faq", "ขอบคุณครับ แล้วส่งฟรีไหม", "ขอบคุณครับ"),
    ("faq", "thank you มีสีขาวไหม", "thankyou"),
    ("faq", "thanks ราคาเท่าไหร่", "thanks"),
    # resume_ai: ต้องตรงทั้งข้อความ
    ("resume_ai", "คุยกับ AI หน่อย หาตู้เย็น", "คุยกับai"),
    ("resume_ai", "กลับไปคุยกับ AI แล้วหาตู้เย็นให้หน่อย", "กลับไปคุยกับai"),
    ("resume_ai", "คุยกับบอทไม่รู้เรื่อง ขอคนจริง", "คุยกับบอท"),
]

# คำที่ตั้งใจไม่ใส่ในไฟล์ (เหตุผลอยู่ในหัวไฟล์) — กันใครเพิ่มกลับโดยไม่ดูเคสพวกนี้
NOT_ENTRIES: list[tuple[str, str]] = [
    ("escalate", "แอดมิน"),
    ("escalate", "สินค้าเสีย"),
    ("escalate", "ของเสีย"),
    ("faq", "ดีค่ะ"),
    ("faq", "ดีครับ"),
]


@pytest.mark.parametrize(("rule_file", "message", "entry"), MUST_MATCH)
def test_must_match(rule_file: str, message: str, entry: str) -> None:
    assert entry in hits(rule_file, message)


@pytest.mark.parametrize(("rule_file", "message", "entry"), MUST_NOT_MATCH)
def test_must_not_match(rule_file: str, message: str, entry: str) -> None:
    assert hits(rule_file, message) == set(), f"ไม่ควรจับ (ทดสอบ {entry})"


@pytest.mark.parametrize("rule_file", ["escalate", "faq", "resume_ai"])
def test_every_entry_has_cases(rule_file: str) -> None:
    file_entries = entries(rule_file)
    matched = {e for f, _, e in MUST_MATCH if f == rule_file}
    not_matched = {e for f, _, e in MUST_NOT_MATCH if f == rule_file}
    assert file_entries, f"{rule_file} ว่าง"
    assert file_entries - matched == set(), "รายการที่ยังไม่มีเคสต้องจับ"
    assert file_entries - not_matched == set(), "รายการที่ยังไม่มีเคสต้องไม่จับ"
    # เคสที่อ้างรายการซึ่งไม่มีในไฟล์แล้ว = เคสค้าง ต้องลบหรือแก้
    assert (matched | not_matched) - file_entries == set()


@pytest.mark.parametrize(("rule_file", "word"), NOT_ENTRIES)
def test_dropped_words_stay_out(rule_file: str, word: str) -> None:
    assert word not in entries(rule_file)


@pytest.mark.parametrize(
    ("message", "answer"),
    [
        ("สวัสดี", GREETING),
        ("สวัสดีค่ะ", GREETING),
        ("สวัสดีครับ", GREETING),
        ("hello", GREETING),
        ("hi", GREETING),
        ("ขอบคุณ", THANKS),
        ("ขอบคุณค่ะ", THANKS),
        ("ขอบคุณครับ", THANKS),
        ("thank you", THANKS),
        ("thanks", THANKS),
    ],
)
def test_faq_answers(message: str, answer: str) -> None:
    assert FAQ[normalize(message)] == answer


def test_faq_has_no_vendor1_keywords_yet() -> None:
    # รอคำตอบ: Vendor 1 ยังตอบ keyword อะไร — ได้คำตอบแล้วเพิ่มบรรทัด "keyword<TAB>-" และลบเทสต์นี้
    assert all(answer is not None for answer in FAQ.values())


@pytest.mark.parametrize("name", ["escalate.txt", "faq.txt", "resume_ai.txt"])
def test_files_written_in_normalized_form(name: str) -> None:
    """หัวไฟล์บอกว่าเขียนในรูปที่ normalize แล้ว — คนอ่านไฟล์ต้องเห็นตรงกับที่โปรแกรมจับ"""
    text = (RULES_DIR / name).read_text(encoding="utf-8-sig")
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        pattern = line.split("\t", 1)[0]
        assert pattern == normalize(pattern), f"{name}: {pattern!r}"
