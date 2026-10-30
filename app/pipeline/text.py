"""app/pipeline/text.py

normalize ข้อความก่อนจับคำ และอ่านไฟล์กฎใน prompts/rules/

รายละเอียด:
 - normalize: NFC -> ลบ zero-width (U+200B, U+200C, U+200D, U+2060, U+FEFF) -> ลบ whitespace ทั้งหมด -> casefold
   ภาษาไทยไม่มีช่องว่างคั่นคำ จึงลบช่องว่างทิ้งหมด ("คุย กับ AI" = "คุยกับai")
 - load_rule_lines: หนึ่งบรรทัดหนึ่งรายการ ข้ามบรรทัดว่างและ # แล้ว normalize ทุกบรรทัด
 - load_faq: บรรทัดละ pattern<TAB>คำตอบ; คำตอบ "-" = keyword ของ Vendor 1 (บอทไม่ตอบ) -> None
 - ไฟล์ผิดรูปแบบ -> ValueError บอกเลขบรรทัด (ไฟล์กฎผิดต้องรู้ตั้งแต่เริ่ม ไม่เงียบ)
 - อ่านแบบ utf-8-sig กัน BOM จาก editor บน Windows

แนวคิดจาก: src/pipeline/filter.js (แบบเก่า), docs/PLAN.md หัวข้อ app/pipeline/text.py
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

ZERO_WIDTH_CHARS = "​‌‍⁠﻿"
_ZERO_WIDTH_TABLE = str.maketrans("", "", ZERO_WIDTH_CHARS)
VENDOR1_ANSWER = "-"


def normalize(s: str) -> str:
    """NFC -> ลบ zero-width -> ลบ whitespace ทั้งหมด (รวม NBSP, ขึ้นบรรทัด) -> casefold"""
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_ZERO_WIDTH_TABLE)
    s = "".join(s.split())
    return s.casefold()


def _content_lines(path: str | Path) -> list[tuple[int, str]]:
    """(เลขบรรทัด, เนื้อหา) ที่ไม่ว่างและไม่ใช่คอมเมนต์ (# หลังตัดช่องว่างหน้า)"""
    text = Path(path).read_text(encoding="utf-8-sig")
    out: list[tuple[int, str]] = []
    for no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append((no, line))
    return out


def load_rule_lines(path: str | Path) -> list[str]:
    """รายการคำในไฟล์กฎ (normalize แล้ว) เรียงตามไฟล์ ตัดตัวซ้ำ"""
    seen: dict[str, None] = {}
    for _, line in _content_lines(path):
        item = normalize(line)
        if item:
            seen.setdefault(item, None)
    return list(seen)


def load_faq(path: str | Path) -> dict[str, str | None]:
    """{pattern ที่ normalize แล้ว: คำตอบ} — คำตอบ "-" -> None (keyword ของ Vendor 1 บอทไม่ตอบ)

    ยก ValueError ถ้าบรรทัดไม่มี TAB, pattern/คำตอบว่าง หรือ pattern ซ้ำ
    """
    faq: dict[str, str | None] = {}
    for no, line in _content_lines(path):
        if "\t" not in line:
            raise ValueError(f"{path}:{no}: ต้องเป็น pattern<TAB>คำตอบ")
        raw_pattern, raw_answer = line.split("\t", 1)
        pattern = normalize(raw_pattern)
        answer = raw_answer.strip()
        if not pattern or not answer:
            raise ValueError(f"{path}:{no}: pattern หรือคำตอบว่าง")
        if pattern in faq:
            raise ValueError(f"{path}:{no}: pattern ซ้ำ: {pattern}")
        faq[pattern] = None if answer == VENDOR1_ANSWER else answer
    return faq
