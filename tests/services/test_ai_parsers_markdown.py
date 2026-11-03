"""tests/services/test_ai_parsers_markdown.py

เคสทดสอบของ MarkdownParser (app/services/ai_parsers.py) กับคำตอบจริงของ AI API + เคสสังเคราะห์

รายละเอียด:
 - fixtures/ai_responses/*.json = คำตอบจริงของ gateway (01-07: 2026-10-23, 08-10: 2026-10-26, D-011)
   ข้อมูลแคตตาล็อกสาธารณะ + คำถามทดสอบ ไม่มีข้อมูลลูกค้า; `_meta` = status, latency_s, request_messages (ตัดออกก่อนส่งให้ parser)
 - สินค้า: sku / ราคาตรงกับ fixture ทุกตัว, ก้อนที่ถูกตัดก่อนครบทิ้ง; ลิงก์สร้างจาก sku เสมอ ไม่ใช้ลิงก์ของ AI
   (08: มีแชทย้อนหลัง AI ตอบ "No product link" ทั้งรายการ, 09: AI เขียนโดเมนผิด, 10: มีชื่อรองใต้หัวสินค้า)
 - kind: products / clarify ("อยากได้ของ") / answer (เวลาเปิดร้าน, เคลม)
 - intro ไม่มีเครื่องหมาย markdown
 - สังเคราะห์: ไม่มีรายการ, รายการที่ไม่มีสินค้าครบ, รูป, ลิงก์ของ AI ผิด/ไม่มี, sku ซ้ำ, ข้อความต่อท้ายรายการ

แนวคิดจาก: src/ai/softnixParser.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว — 10 ตัวอย่างจริง (เป้า 20-30)
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.pipeline.schemas import AiBadResponse, AiKind, AiResult
from app.services.ai_parsers import MarkdownParser, to_line_text

FIXTURES = Path(__file__).parent / "fixtures" / "ai_responses"
FIXTURE_NAMES = sorted(p.stem for p in FIXTURES.glob("*.json"))

# เครื่องหมาย markdown ที่ต้องไม่หลุดไปถึงลูกค้า
_MD_MARKERS = re.compile(r"\*\*|^#+\s|!\[|\]\(|^\s*\*\s", re.MULTILINE)


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def body_of(name: str) -> bytes:
    data = load(name)
    data.pop("_meta")
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def content_of(name: str) -> str:
    return load(name)["choices"][0]["message"]["content"]


def parse(body: bytes | str) -> AiResult:
    return MarkdownParser().parse(body, latency_ms=5, attempts=1)


def chat(content: str) -> str:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})


def block(n: int, name: str, sku: str, price: str, *, image: str | None = None, link: str | None = None) -> str:
    lines = [f"### {n}. {name}"]
    if image:
        lines.append(f"![{name}]({image})")
    lines += [
        "- Brand: TEST",
        f"- Price: ฿{price}",
        "- Details: ทดสอบ",
        f"- Product code: {sku}",
        f"- Product link: [View product details]({link or f'https://www.homepro.co.th/p/{sku}'})",
    ]
    return "\n".join(lines)


# --- คำตอบจริง ---


def test_fixtures_present():
    assert len(FIXTURE_NAMES) == 10


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_parses_and_is_line_safe(name):
    meta = load(name)["_meta"]
    assert meta["status"] == 200 and meta["request_messages"]
    result = parse(body_of(name))
    assert result.kind in (AiKind.PRODUCTS, AiKind.ANSWER, AiKind.CLARIFY)
    assert result.escalate_hint is None and result.state_out is None
    assert (result.latency_ms, result.attempts) == (5, 1)
    assert result.raw and result.raw == content_of(name)[: len(result.raw)]
    for text in (result.intro, result.clarify_question):
        if text is not None:
            assert text.strip() == text and text
            assert not _MD_MARKERS.search(text), text
            assert "\n\n\n" not in text
    for p in result.products:
        assert p.url.startswith("https://www.homepro.co.th/p/") and p.url.endswith(p.sku)
        assert p.price is not None and p.price > 0
        assert p.image_url is None or p.image_url.startswith("https://")


TRAP_PRODUCTS = [
    ("1033871", "689.00"),
    ("1043693", "307.00"),
    ("1030372", "165.00"),
    ("1021695", "810.00"),
    ("1053536", "329.00"),
]

# (ชื่อไฟล์, [(sku, ราคา)]) — 01/02/06 ก้อนที่ 5 ถูกตัดก่อนมีราคา (ทิ้ง);
# 07 ก้อนที่ 3 ถูกตัดที่ลิงก์แต่ชื่อ/ราคา/รหัสครบแล้ว (เก็บ); 08/09 ครบทั้ง 5 ก้อน
EXPECTED_PRODUCTS = {
    "01_products_fridge": [
        ("1289220", "14999.00"),
        ("1225597", "18990.00"),
        ("1241238", "22490.00"),
        ("1225613", "18990.00"),
    ],
    "02_refine": [
        ("1266175", "15990.00"),
        ("1266157", "18990.00"),
        ("1266167", "12490.00"),
        ("1216191", "18990.00"),
    ],
    "06_topic_change": [
        ("1084087", "25990.00"),
        ("1319426", "15490.00"),
        ("1297952", "14990.00"),
        ("1260654", "16999.00"),
    ],
    "07_compare": [("1249051", "16990.00"), ("1281772", "34990.00"), ("1189182", "31990.00")],
    "08_history_no_product_link": TRAP_PRODUCTS,
    "09_wrong_link_domain": TRAP_PRODUCTS,
    "10_subtitle_line": [
        ("1319426", "15490.00"),
        ("1319441", "29990.00"),
        ("1319420", "12990.00"),
        ("1319427", "18990.00"),
        ("1232663", "7990.00"),
    ],
}
# จำนวนก้อนที่ถูกตัดจนไม่ครบ (ทิ้ง) ต่อไฟล์
DROPPED_BLOCKS = {"01_products_fridge": 1, "02_refine": 1, "06_topic_change": 1}


@pytest.mark.parametrize("name", sorted(EXPECTED_PRODUCTS))
def test_products_exact_and_incomplete_block_dropped(name):
    result = parse(body_of(name))
    assert result.kind is AiKind.PRODUCTS
    assert [(p.sku, p.price) for p in result.products] == [
        (sku, Decimal(price)) for sku, price in EXPECTED_PRODUCTS[name]
    ]
    for p in result.products:
        assert p.url == f"https://www.homepro.co.th/p/{p.sku}"
    blocks = content_of(name).count("\n### ")
    assert blocks == len(EXPECTED_PRODUCTS[name]) + DROPPED_BLOCKS.get(name, 0)


def test_fridge_names_and_intro():
    result = parse(body_of("01_products_fridge"))
    assert result.products[0].name == "ตู้เย็น 2 ประตู HISENSE RT549N4TBN 15 คิว สีดำ"
    assert result.intro == (
        "สวัสดีค่ะ มีตู้เย็น 2 ประตูให้เลือกหลายรุ่นเลยค่ะ โดยมีทั้งขนาดเล็กและขนาดใหญ่ตามการใช้งาน ดังนี้ค่ะ"
    )
    assert all("TOSHI" not in p.name for p in result.products)  # ก้อนที่ถูกตัด "### 5. ... TOSHI"


@pytest.mark.parametrize("name", ["02_refine", "06_topic_change"])
def test_list_without_intro_has_no_intro(name):
    assert parse(body_of(name)).intro is None


def test_brand_and_details_from_real_answer():
    """01: "- Brand:" / "- Details:" ขึ้นการ์ด (ไม่ใช้ตรวจราคา/ลิงก์)"""
    p = parse(body_of("01_products_fridge")).products[0]
    assert p.brand == "HISENSE"
    assert p.details == "ระบบ Inverter, รับประกันคอมเพรสเซอร์ 25 ปี"


def test_brand_and_details_optional_and_markdown_stripped():
    content = "\n".join(
        [
            "## Product List",
            "### 1. ตู้เย็น",
            "- Price: ฿1,000",
            "- **Details:** **ประหยัดไฟ** [ดูสเปก](https://evil.example/x)",
            "- Product code: 111",
            "### 2. พัดลม",
            "- Price: ฿500",
            "- Product code: 222",
            "",
        ]
    )
    first, second = parse(chat(content)).products
    assert (first.brand, first.details) == (None, "ประหยัดไฟ ดูสเปก")
    assert (second.brand, second.details) == (None, None)


def test_washing_machine_block_cut_at_link_kept_with_image():
    """07: ก้อนที่ 3 ถูกตัดที่ "[View product" — ชื่อ/ราคา/รหัส/รูปครบแล้ว ลิงก์สร้างจากรหัส"""
    content = content_of("07_compare")
    assert "![" in content and content.endswith("[View product")
    p = parse(body_of("07_compare")).products[-1]
    assert (p.sku, p.price) == ("1189182", Decimal("31990.00"))
    assert p.url == "https://www.homepro.co.th/p/1189182"
    assert p.image_url == (
        "https://statice.homepro.co.th/homepro/ARTICLE_IMAGE/11/891/1189182/447x447/26012024_1189182$Imagec1.jpg"
    )
    assert p.name == "เครื่องซักผ้าฝาหน้า ELECTROLUX EWF1024P5WB 10 กก."


def test_no_product_link_from_ai_still_gives_products():
    """08: มีแชทย้อนหลัง (รวม "ระบบขัดข้อง") -> AI ตอบ "Product link: No product link" ทุกก้อน"""
    assert content_of("08_history_no_product_link").count("Product link: No product link") == 5
    result = parse(body_of("08_history_no_product_link"))
    assert result.kind is AiKind.PRODUCTS and len(result.products) == 5
    assert result.intro is None


def test_wrong_link_domain_from_ai_replaced():
    """09: AI เขียน "https://www.home/p/1033871" (โดเมนผิด) -> ใช้ลิงก์จากรหัสแทน"""
    assert "https://www.home/p/1033871" in content_of("09_wrong_link_domain")
    p = parse(body_of("09_wrong_link_domain")).products[0]
    assert p.url == "https://www.homepro.co.th/p/1033871"


def test_subtitle_line_under_heading_does_not_end_block():
    """10: ใต้ "### 1. ทีวีแอลอีดี 55 นิ้ว ..." มีบรรทัด "LED TV 55” SAMSUNG ..." ก่อนช่องแรก
    เดิมนับเป็นข้อความท้ายก้อน -> ทิ้งทุกก้อน แล้วส่งรายการทั้งก้อนเป็นข้อความธรรมดา"""
    assert "\nLED TV 55” SAMSUNG" in content_of("10_subtitle_line")
    result = parse(body_of("10_subtitle_line"))
    assert result.kind is AiKind.PRODUCTS
    assert result.products[0].name == "ทีวีแอลอีดี 55 นิ้ว SAMSUNG (4K, LED, TIZEN) UA55UE800HKXXT"
    assert result.intro is None


def test_plain_text_after_fields_still_ends_block():
    content = "## Product List\n\n" + block(1, "A", "1", "100") + "\nสนใจรุ่นไหนคะ\n- Price: ฿5\n"
    result = parse(chat(content))
    assert [(p.sku, p.price) for p in result.products] == [("1", Decimal("100"))]


def test_vague_question_is_clarify():
    result = parse(body_of("03_vague"))
    assert result.kind is AiKind.CLARIFY
    assert result.intro is None and result.products == []
    assert result.clarify_question is not None
    assert result.clarify_question.startswith("สวัสดีค่ะ ยินดีต้อนรับสู่ HomePro ค่ะ")
    assert "?" in result.clarify_question


@pytest.mark.parametrize("name", ["04_claim", "05_nonproduct"])
def test_non_product_questions_are_answer(name):
    result = parse(body_of(name))
    assert result.kind is AiKind.ANSWER
    assert result.products == [] and result.clarify_question is None
    assert result.intro


def test_store_hours_text_kept():
    assert "09:00 น. ถึง 21:00 น." in (parse(body_of("05_nonproduct")).intro or "")


def test_claim_answer_bold_removed_and_lists_kept():
    intro = parse(body_of("04_claim")).intro or ""
    assert "**" in content_of("04_claim") and "**" not in intro
    assert "1. ติดต่อผ่าน Home Service: หากสินค้า" in intro
    assert "\n- ใบเสร็จรับเงิน หรือ ใบกำกับภาษี" in intro
    assert "เอกสารที่ควรเตรียมไว้:" in intro


# --- สังเคราะห์ ---


def test_no_list_plain_answer():
    result = parse(chat("ร้านเปิด 9 โมงค่ะ"))
    assert (result.kind, result.intro) == (AiKind.ANSWER, "ร้านเปิด 9 โมงค่ะ")


@pytest.mark.parametrize("mark", ["?", "？"])
def test_question_mark_at_end_is_clarify(mark):
    result = parse(chat(f"ต้องการขนาดกี่คิวคะ{mark}"))
    assert result.kind is AiKind.CLARIFY
    assert result.clarify_question == f"ต้องการขนาดกี่คิวคะ{mark}"


def test_question_mark_mid_line_is_not_clarify():
    assert parse(chat("รุ่น A? มีค่ะ ราคาดูที่ร้าน")).kind is AiKind.ANSWER


def test_list_with_zero_complete_products_and_intro_is_answer():
    content = "มีหลายรุ่นค่ะ ดังนี้\n\n## Product List\n\n### 1. ตู้เย็น\n- Brand: X\n- Price: ฿9,990.00\n- Product co"
    result = parse(chat(content))
    assert result.kind is AiKind.ANSWER
    assert result.intro == "มีหลายรุ่นค่ะ ดังนี้"
    assert result.products == []


def test_list_with_zero_complete_products_and_no_intro_is_bad_response():
    with pytest.raises(AiBadResponse) as ei:
        parse(chat("## Product List\n\n### 1. ตู้เย็น 2 ประตู TOSHI"))
    assert ei.value.error_class == "parse"
    assert ei.value.latency_ms == 5


def test_complete_last_line_link_without_newline_is_kept():
    content = "## Product List\n\n" + block(1, "พัดลม", "111", "990.00")
    assert [p.sku for p in parse(chat(content)).products] == ["111"]


def test_price_or_code_on_cut_last_line_drops_block():
    # ลิงก์มาก่อน ราคาอยู่บรรทัดสุดท้ายที่ไม่มี newline ปิด -> อาจถูกตัด ("฿1" จาก "฿1,290") -> ทิ้ง
    content = (
        "## Product List\n\n### 1. พัดลม\n- Product link: [x](https://www.homepro.co.th/p/111)\n"
        "- Product code: 111\n- Price: ฿1"
    )
    with pytest.raises(AiBadResponse):
        parse(chat(content))
    assert [p.price for p in parse(chat(content + ",290.00\n")).products] == [Decimal("1290.00")]


@pytest.mark.parametrize(
    "link",
    [
        "http://www.homepro.co.th/p/111",  # ไม่ใช่ https
        "javascript:alert(1)",
        "https://",
        "https://evil.example/p/111",
        "https://www.homepro.co.th/p/999",  # รหัสไม่ตรง
    ],
)
def test_ai_product_link_ignored(link):
    content = "เกริ่น\n\n## Product List\n\n" + block(1, "พัดลม", "111", "990", link=link) + "\n"
    (p,) = parse(chat(content)).products
    assert p.url == "https://www.homepro.co.th/p/111"


def test_missing_link_line_still_kept():
    content = "## Product List\n\n### 1. พัดลม\n- Price: ฿990\n- Product code: 111\n"
    (p,) = parse(chat(content)).products
    assert p.url == "https://www.homepro.co.th/p/111"


def test_sku_escaped_in_url():
    content = "## Product List\n\n### 1. พัดลม\n- Price: ฿990\n- Product code: A/1?x#\n"
    (p,) = parse(chat(content)).products
    assert p.url == "https://www.homepro.co.th/p/A%2F1%3Fx%23"


def test_non_https_image_ignored_but_product_kept():
    content = "## Product List\n\n" + block(1, "พัดลม", "111", "990", image="http://img.example/x.jpg") + "\n"
    (p,) = parse(chat(content)).products
    assert p.image_url is None


def test_link_on_cut_last_line_does_not_matter():
    content = "เกริ่น\n\n## Product List\n\n### 1. พัดลม\n- Price: ฿990\n- Product code: 111\n- Product link: https://www.homepro.co.th/p/11"
    (p,) = parse(chat(content)).products
    assert p.url == "https://www.homepro.co.th/p/111"


def test_code_on_cut_last_line_drops_block():
    content = "เกริ่น\n\n## Product List\n\n### 1. พัดลม\n- Price: ฿990\n- Product code: 11"
    assert parse(chat(content)).products == []


def test_duplicate_sku_keeps_first():
    content = "## Product List\n\n" + block(1, "A", "111", "100") + "\n\n" + block(2, "B", "111", "200") + "\n"
    result = parse(chat(content))
    assert [(p.name, p.price) for p in result.products] == [("A", Decimal("100"))]


def test_missing_price_drops_block():
    content = (
        "## Product List\n\n### 1. A\n- Price: สอบถามราคา\n- Product code: 1\n"
        "- Product link: [x](https://www.homepro.co.th/p/1)\n\n" + block(2, "B", "2", "200") + "\n"
    )
    assert [p.sku for p in parse(chat(content)).products] == ["2"]


def test_trailing_text_after_list_not_in_intro_when_products():
    content = "เกริ่นค่ะ\n\n## Product List\n\n" + block(1, "A", "1", "100") + "\n\nสนใจรุ่นไหนบอกได้นะคะ?\n"
    result = parse(chat(content))
    assert result.kind is AiKind.PRODUCTS
    assert result.intro == "เกริ่นค่ะ"


def test_trailing_text_used_when_no_complete_product():
    content = "## Product List\n\n### 1. A\n- Price: ฿100\n\nขออภัย ไม่พบสินค้าที่ตรงค่ะ\n"
    result = parse(chat(content))
    assert (result.kind, result.intro) == (AiKind.ANSWER, "ขออภัย ไม่พบสินค้าที่ตรงค่ะ")


def test_bold_field_names_and_product_name():
    content = (
        "## Product List\n\n### 1. **พัดลม** ตั้งพื้น\n- **Price:** ฿1,290.00\n- **Product code:** `222`\n"
        "- **Product link:** [ดูสินค้า](https://www.homepro.co.th/p/222)\n"
    )
    (p,) = parse(chat(content)).products
    assert (p.sku, p.name, p.price) == ("222", "พัดลม ตั้งพื้น", Decimal("1290.00"))


def test_raw_is_content_truncated():
    result = parse(chat("ก" * 10_000))
    assert result.raw.endswith("…[truncated]")
    assert len(result.raw) < 5000


def test_parse_error_does_not_echo_content():
    secret = "0812345678"
    with pytest.raises(AiBadResponse) as ei:
        parse(json.dumps({"choices": [{"message": {"content": f"## Product List\n### 1. {secret}"}}]}))
    assert secret not in str(ei.value)


def test_accepts_str_and_crlf():
    result = parse(chat("บรรทัดแรก\r\n\r\n\r\n\r\nบรรทัดสอง"))
    assert result.intro == "บรรทัดแรก\n\nบรรทัดสอง"


# --- to_line_text ---


@pytest.mark.parametrize(
    ("md", "keep", "expected"),
    [
        ("## หัวข้อ\nเนื้อหา", True, "หัวข้อ\nเนื้อหา"),
        ("**ตัวหนา** และ *เอียง*", True, "ตัวหนา และ เอียง"),
        ("ดู [ที่นี่](https://x.example/a) ค่ะ", True, "ดู ที่นี่ (https://x.example/a) ค่ะ"),
        ("ดู [ที่นี่](https://x.example/a) ค่ะ", False, "ดู ที่นี่ ค่ะ"),
        ("[https://x.example/a](https://x.example/a)", True, "https://x.example/a"),
        ("รูป ![alt](https://x.example/i.jpg) จบ", True, "รูป  จบ"),
        ("* หนึ่ง\n* สอง", True, "- หนึ่ง\n- สอง"),
        ("ก\n\n\n\nข", True, "ก\n\nข"),
        ("ก\n---\nข", True, "ก\n\nข"),
        ("ใช้ `code` ได้", True, "ใช้ code ได้"),
        ("ราคา 2*3 = 6", True, "ราคา 2*3 = 6"),
    ],
)
def test_to_line_text(md, keep, expected):
    assert to_line_text(md, keep_link_urls=keep) == expected
