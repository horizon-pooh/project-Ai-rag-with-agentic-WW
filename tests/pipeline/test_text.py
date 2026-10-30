"""tests/pipeline/test_text.py — app/pipeline/text.py: normalize และตัวอ่านไฟล์กฎ"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from app.pipeline.text import load_faq, load_rule_lines, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("คุยกับ AI", "คุยกับai"),
        (" คุย กับ ai ", "คุยกับai"),
        ("คุยกับAI", "คุยกับai"),
        ("คุย​กับ‌A‍I⁠﻿", "คุยกับai"),  # zero-width ทุกตัว
        ("คุย กับ\tAI\n", "คุยกับai"),  # NBSP, tab, ขึ้นบรรทัด
        ("ตู้เย็น  2  ประตู", "ตู้เย็น2ประตู"),
        ("ＡＩ", "ａｉ"),  # casefold แต่ไม่ทำ NFKC (ตั้งใจตามสเปก)
        ("", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_nfc() -> None:
    # สระอำแยกส่วน (นิคหิต + สระอา) กับ é แบบ decomposed -> NFC
    decomposed = unicodedata.normalize("NFD", "é")
    assert normalize(decomposed) == "é"
    assert normalize("คุยกับ AI") == normalize(unicodedata.normalize("NFD", "คุยกับ AI"))


def test_normalize_does_not_merge_different_words() -> None:
    assert normalize("ตู้เย็น") != normalize("ตู้เสื้อผ้า")


def test_load_rule_lines(tmp_path: Path) -> None:
    p = tmp_path / "rules.txt"
    p.write_text(
        "﻿# คอมเมนต์\n\nคุยกับ AI\n  # คอมเมนต์ย่อหน้า\nคุยกับai\nขอ คุย กับ คน\n   \n",
        encoding="utf-8",
    )
    assert load_rule_lines(p) == ["คุยกับai", "ขอคุยกับคน"]


def test_load_real_resume_ai_file() -> None:
    assert "คุยกับai" in load_rule_lines(Path("prompts/rules/resume_ai.txt"))


def test_load_faq(tmp_path: Path) -> None:
    p = tmp_path / "faq.txt"
    p.write_text(
        "# pattern<TAB>คำตอบ\nสวัสดี\tสวัสดีค่ะ ต้องการหาสินค้าอะไรคะ\nโปร โมชั่น\t-\n",
        encoding="utf-8",
    )
    assert load_faq(p) == {"สวัสดี": "สวัสดีค่ะ ต้องการหาสินค้าอะไรคะ", "โปรโมชั่น": None}


@pytest.mark.parametrize(
    "content",
    ["สวัสดี สวัสดีค่ะ\n", "\tคำตอบ\n", "สวัสดี\t \n", "สวัสดี\tก\nสวัส ดี\tข\n"],
    ids=["no-tab", "empty-pattern", "empty-answer", "duplicate"],
)
def test_load_faq_rejects_bad_lines(tmp_path: Path, content: str) -> None:
    p = tmp_path / "faq.txt"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_faq(p)
