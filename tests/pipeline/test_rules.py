"""tests/pipeline/test_rules.py

เคสทดสอบของ app/pipeline/steps/rules.py

รายละเอียด:
 - ใช้ไฟล์กฎชั่วคราว (tmp_path) ของเทสต์เอง ไม่อ่าน prompts/rules จริง
   (เคสของทุกคำในไฟล์จริงอยู่ที่เทสต์ของไฟล์กฎ)
 - ลำดับความสำคัญ: postback -> escalate -> ว่าง -> ถี่ -> Vendor 1 -> FAQ -> CONTINUE
 - escalate = substring หลัง normalize; faq = ตรงทั้งข้อความหลัง normalize
 - กับดักภาษาไทย ("ตู้เย็น" vs "ตู้เสื้อผ้า"), zero-width, ช่องว่าง
 - ขอบของ RATE_LIMIT_PER_MIN

แนวคิดจาก: ใหม่ (docs/PLAN.md การจับคำ)
สถานะ: มีโค้ดแล้ว (งาน 4)
"""

from __future__ import annotations

import pytest

from app.pipeline import replies
from app.pipeline.schemas import RuleOutcome
from app.pipeline.steps.rules import RuleSet, apply_rules, is_risky, load_rule_set
from tests.fakes import make_event, make_settings

ESCALATE_TXT = """\
# ไฟล์ทดสอบ
ร้องเรียน
เคลม
ตู้เย็น เสีย
ขอคุยกับคน
"""

FAQ_TXT = (
    "# ไฟล์ทดสอบ\n"
    "สวัสดี\tสวัสดีค่ะ ต้องการหาสินค้าอะไรคะ\n"
    "ตู้เย็น\tมีตู้เย็นหลายรุ่นค่ะ\n"
    "เวลาเปิดร้าน\t-\n"
    "เคลม\tคำตอบนี้ต้องไม่ถูกใช้ (escalate มาก่อน)\n"
)

SETTINGS = make_settings(rate_limit_per_min=10)
ZWSP = "​"


@pytest.fixture(scope="module")
def rules(tmp_path_factory) -> RuleSet:
    d = tmp_path_factory.mktemp("rules")
    (d / "escalate.txt").write_text(ESCALATE_TXT, encoding="utf-8")
    (d / "faq.txt").write_text(FAQ_TXT, encoding="utf-8")
    return load_rule_set(d)


def _apply(rules: RuleSet, *, count: int = 1, has_image: bool = False, **event_kw):
    return apply_rules(
        make_event(**event_kw),
        rules,
        recent_inbound_count=count,
        settings=SETTINGS,
        has_image=has_image,
    )


# --- load ---


def test_load_rule_set_normalizes(rules):
    assert rules.escalate == ("ร้องเรียน", "เคลม", "ตู้เย็นเสีย", "ขอคุยกับคน")
    assert rules.faq["สวัสดี"] == "สวัสดีค่ะ ต้องการหาสินค้าอะไรคะ"
    assert rules.faq["เวลาเปิดร้าน"] is None


def test_load_rule_set_missing_file_raises(tmp_path):
    (tmp_path / "escalate.txt").write_text("เคลม\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_rule_set(tmp_path)


def test_rule_set_faq_is_read_only(rules):
    with pytest.raises(TypeError):
        rules.faq["ใหม่"] = "x"  # type: ignore[index]


# --- postback ---


def test_postback_detail_returns_sku(rules):
    r = _apply(rules, postback="action=detail&sku=RF-123")
    assert r.outcome is RuleOutcome.PRODUCT_DETAIL
    assert r.sku == "RF-123"


def test_postback_detail_without_sku_ignored(rules):
    r = _apply(rules, postback="action=detail")
    assert r.outcome is RuleOutcome.IGNORE
    assert r.reason == "detail_without_sku"


def test_postback_restart(rules):
    assert _apply(rules, postback="action=restart").outcome is RuleOutcome.RESTART


def test_postback_feedback(rules):
    r = _apply(rules, postback="action=feedback&v=down&run=5")
    assert r.outcome is RuleOutcome.FEEDBACK


@pytest.mark.parametrize("data", ["action=unknown", "foo=bar", "", "action=talk_to_staff"])
def test_unknown_postback_ignored(rules, data):
    r = _apply(rules, postback=data)
    assert r.outcome is RuleOutcome.IGNORE
    assert r.reason == "unknown_postback"


def test_postback_beats_rate_limit(rules):
    r = _apply(rules, postback="action=restart", count=99)
    assert r.outcome is RuleOutcome.RESTART


# --- escalate (substring) ---


@pytest.mark.parametrize(
    ("text", "entry"),
    [
        ("ร้องเรียน", "ร้องเรียน"),
        ("อยากร้องเรียนพนักงานส่งของค่ะ", "ร้องเรียน"),
        ("เคลมสินค้าได้ไหม", "เคลม"),
        ("ตู้เย็นเสียค่ะ", "ตู้เย็นเสีย"),
        ("ตู้เย็น เสีย ทำไงดี", "ตู้เย็นเสีย"),
        ("ขอ คุย กับ คน หน่อย", "ขอคุยกับคน"),
        (f"เค{ZWSP}ลม", "เคลม"),
        ("ร้อง‍เรียน﻿", "ร้องเรียน"),
    ],
)
def test_escalate_substring_after_normalize(rules, text, entry):
    r = _apply(rules, text=text)
    assert r.outcome is RuleOutcome.ESCALATE
    assert r.reason == f"rule:{entry}"
    assert is_risky(text, rules) is True


@pytest.mark.parametrize(
    "text",
    [
        "ตู้เสื้อผ้าเสีย",  # กับดัก: "ตู้" เหมือนกันแต่ไม่ใช่ตู้เย็น
        "ตู้เย็นสีเขียว",  # มีคำว่าตู้เย็นแต่ไม่เสีย
        "เสียงตู้เย็นดัง",  # คำเดียวกันแต่สลับลำดับ ไม่เป็น substring
        "หาตู้เสื้อผ้า",
    ],
)
def test_escalate_thai_traps_not_matched(rules, text):
    assert _apply(rules, text=text).outcome is RuleOutcome.CONTINUE
    assert is_risky(text, rules) is False


def test_first_matching_escalate_entry_is_reason(rules):
    r = _apply(rules, text="ขอเคลมและร้องเรียน")
    assert r.reason == "rule:ร้องเรียน"  # ตามลำดับในไฟล์ ไม่ใช่ตำแหน่งในข้อความ


def test_escalate_beats_faq_and_rate_limit(rules):
    r = _apply(rules, text="เคลม", count=99)
    assert r.outcome is RuleOutcome.ESCALATE


def test_is_risky_none_and_empty(rules):
    assert is_risky(None, rules) is False
    assert is_risky("", rules) is False
    assert is_risky(f" {ZWSP} ", rules) is False


def test_empty_escalate_list_never_risky():
    assert is_risky("ร้องเรียน", RuleSet()) is False


# --- ว่าง ---


@pytest.mark.parametrize("text", ["", "   ", f"{ZWSP}⁠", "\n\t"])
def test_empty_text_replies_empty(rules, text):
    r = _apply(rules, text=text)
    assert r.outcome is RuleOutcome.EMPTY
    assert r.reply_text == replies.EMPTY


def test_sticker_without_text_is_empty(rules):
    assert _apply(rules, message_type="sticker").outcome is RuleOutcome.EMPTY


def test_image_without_text_continues(rules):
    r = _apply(rules, message_type="image", has_image=True)
    assert r.outcome is RuleOutcome.CONTINUE


def test_empty_beats_rate_limit(rules):
    assert _apply(rules, text=" ", count=99).outcome is RuleOutcome.EMPTY


# --- จำกัดอัตรา ---


def test_rate_limit_boundary(rules):
    assert _apply(rules, text="หาทีวี", count=10).outcome is RuleOutcome.CONTINUE
    r = _apply(rules, text="หาทีวี", count=11)
    assert r.outcome is RuleOutcome.RATE_LIMITED


def test_rate_limit_beats_faq(rules):
    assert _apply(rules, text="สวัสดี", count=11).outcome is RuleOutcome.RATE_LIMITED


def test_rate_limit_with_image(rules):
    r = _apply(rules, message_type="image", has_image=True, count=11)
    assert r.outcome is RuleOutcome.RATE_LIMITED


# --- FAQ (ตรงทั้งข้อความ) ---


@pytest.mark.parametrize("text", ["สวัสดี", " สวัสดี ", f"สวัส{ZWSP}ดี", "สวัสดี\n"])
def test_faq_exact_after_normalize(rules, text):
    r = _apply(rules, text=text)
    assert r.outcome is RuleOutcome.REPLY
    assert r.reply_text == "สวัสดีค่ะ ต้องการหาสินค้าอะไรคะ"


def test_faq_casefold_latin():
    rs = RuleSet(faq={"hello": "hi"})
    r = apply_rules(make_event(text="HeLLo"), rs, recent_inbound_count=1, settings=SETTINGS)
    assert r.outcome is RuleOutcome.REPLY and r.reply_text == "hi"


@pytest.mark.parametrize("text", ["สวัสดีค่ะ หาตู้เย็น", "ตู้เย็นสีเขียว", "ตู้เสื้อผ้า", "ตู้"])
def test_faq_not_substring(rules, text):
    assert _apply(rules, text=text).outcome is RuleOutcome.CONTINUE


def test_faq_thai_exact_hit(rules):
    r = _apply(rules, text="ตู้เย็น")
    assert r.outcome is RuleOutcome.REPLY
    assert r.reply_text == "มีตู้เย็นหลายรุ่นค่ะ"


def test_vendor1_keyword_ignored(rules):
    r = _apply(rules, text="เวลา เปิดร้าน")
    assert r.outcome is RuleOutcome.IGNORE
    assert r.reason == "vendor1"
    assert r.reply_text is None


def test_plain_text_continues(rules):
    r = _apply(rules, text="หาทีวี 55 นิ้ว")
    assert r.outcome is RuleOutcome.CONTINUE
    assert r.reason is None and r.reply_text is None
