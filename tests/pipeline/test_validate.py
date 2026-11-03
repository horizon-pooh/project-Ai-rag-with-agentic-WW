"""tests/pipeline/test_validate.py — validate / on_validate_fail (app/pipeline/steps/validate.py)"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.pipeline import replies
from app.pipeline.schemas import (
    LINE_MAX_TEXT_CHARS,
    MAX_CAROUSEL_BUBBLES,
    AiKind,
    AiResult,
    Counters,
    Product,
)
from app.pipeline.steps.call_ai import AiRoute
from app.pipeline.steps.validate import on_validate_fail, reset_validate_streak, validate


def _p(sku: str = "A1", **kw: object) -> Product:
    values: dict[str, object] = {
        "sku": sku,
        "name": f"ตู้เย็น {sku}",
        "url": f"https://shop.example/{sku}",
    }
    values.update(kw)
    return Product(**values)  # type: ignore[arg-type]


def _products(*products: Product, intro: str | None = "เจอแล้วค่ะ") -> AiResult:
    return AiResult(kind=AiKind.PRODUCTS, intro=intro, products=list(products))


def _types(v) -> list[str]:
    assert v.ok, v.errors
    return [m["type"] for m in v.outbound.messages]


# --- ANSWER + products ---


def test_products_build_intro_and_carousel() -> None:
    v = validate(
        _products(_p("A", price=Decimal("100"), image_url="https://img.example/a.jpg"), _p("B")),
        AiRoute.ANSWER,
    )
    assert _types(v) == ["text", "flex"]
    assert v.outbound.purpose == "ai_answer"
    assert v.outbound.messages[0]["text"] == "เจอแล้วค่ะ"
    bubbles = v.outbound.messages[1]["contents"]["contents"]
    assert len(bubbles) == 2


def test_products_without_intro_is_carousel_only() -> None:
    assert _types(validate(_products(_p(), intro="  "), AiRoute.ANSWER)) == ["flex"]


def test_answer_kind_with_products_also_builds_cards() -> None:
    r = AiResult(kind=AiKind.ANSWER, intro="x", products=[_p()])
    assert _types(validate(r, AiRoute.ANSWER)) == ["text", "flex"]


def test_more_than_ten_products_are_capped() -> None:
    v = validate(_products(*[_p(f"S{i}") for i in range(MAX_CAROUSEL_BUBBLES + 3)]), AiRoute.ANSWER)
    bubbles = v.outbound.messages[-1]["contents"]["contents"]
    assert len(bubbles) == MAX_CAROUSEL_BUBBLES


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", "http://shop.example/a"),
        ("url", "javascript:alert(1)"),
        ("url", "https://"),
        ("url", "//shop.example/a"),
        ("url", "shop.example/a"),
        ("url", "https://shop.example/a b"),
        ("url", "https://shop.example/\na"),
        ("url", "ftp://shop.example/a"),
        ("url", "https://shop.example/" + "a" * 1000),
        ("image_url", "http://img.example/a.jpg"),
        ("image_url", "data:image/png;base64,AAAA"),
        ("image_url", ""),
    ],
)
def test_fabricated_or_unsafe_links_fail(field: str, value: str) -> None:
    v = validate(_products(_p(**{field: value})), AiRoute.ANSWER)
    assert not v.ok
    assert any(e.startswith(f"products[0].{field}") for e in v.errors)


def test_uppercase_https_scheme_is_accepted() -> None:
    assert validate(_products(_p(url="HTTPS://shop.example/a")), AiRoute.ANSWER).ok


@pytest.mark.parametrize("price", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_bad_price_fails(price: Decimal) -> None:
    # NaN/Infinity: pydantic กันไว้แล้ว ใช้ model_copy (ไม่ validate) ทดสอบด่านสำรองใน validate
    product = _p().model_copy(update={"price": price})
    v = validate(_products(product), AiRoute.ANSWER)
    assert not v.ok
    assert any("price" in e for e in v.errors)


def test_zero_and_missing_price_ok() -> None:
    assert validate(_products(_p("A", price=Decimal("0")), _p("B")), AiRoute.ANSWER).ok


def test_blank_sku_or_name_fails() -> None:
    v = validate(_products(_p(sku="  "), _p("B", name=" ")), AiRoute.ANSWER)
    assert not v.ok
    assert "products[0].sku: ว่าง" in v.errors
    assert "products[1].name: ว่าง" in v.errors


def test_duplicate_sku_fails() -> None:
    v = validate(_products(_p("A"), _p("B"), _p("A")), AiRoute.ANSWER)
    assert not v.ok
    assert "products[2].sku: ซ้ำ" in v.errors


def test_bad_product_beyond_cap_still_fails() -> None:
    products = [_p(f"S{i}") for i in range(MAX_CAROUSEL_BUBBLES)] + [
        _p("BAD", url="http://x.example")
    ]
    assert not validate(_products(*products), AiRoute.ANSWER).ok


def test_long_sku_ok_without_postback_button() -> None:
    # การ์ดไม่มีปุ่ม postback แล้ว (ตัด "ดูรายละเอียด" 2026-10-26) -> ไม่จำกัดความยาว SKU ตาม postback data
    assert validate(_products(_p("X" * 300)), AiRoute.ANSWER).ok


def _bubbles(v) -> list[dict]:
    assert v.ok, v.errors
    return v.outbound.messages[-1]["contents"]["contents"]


def test_placeholder_image_for_products_without_image() -> None:
    placeholder = "https://bot.example/static/no-image.png"
    v = validate(
        _products(_p("A"), _p("B", image_url="https://img.example/b.jpg")),
        AiRoute.ANSWER,
        placeholder_image_url=placeholder,
    )
    assert [b["hero"]["url"] for b in _bubbles(v)] == [placeholder, "https://img.example/b.jpg"]


def test_no_placeholder_means_no_hero() -> None:
    assert "hero" not in _bubbles(validate(_products(_p()), AiRoute.ANSWER))[0]


def test_details_unknown_url_removed_product_url_kept() -> None:
    details = "สเปกเต็ม https://evil.example/x ดูที่ https://shop.example/A1"
    (bubble,) = _bubbles(validate(_products(_p(details=details)), AiRoute.ANSWER))
    shown = bubble["body"]["contents"][1]["text"]
    assert "evil.example" not in shown
    assert shown.startswith("สเปกเต็ม") and shown.endswith("ดูที่ https://shop.example/A1")


def test_details_only_unknown_url_is_dropped() -> None:
    (bubble,) = _bubbles(validate(_products(_p(details="https://evil.example/x")), AiRoute.ANSWER))
    assert [c["text"] for c in bubble["body"]["contents"]] == ["ตู้เย็น A1", "รหัสสินค้า A1"]


def test_intro_too_long_fails() -> None:
    v = validate(_products(_p(), intro="ก" * (LINE_MAX_TEXT_CHARS + 1)), AiRoute.ANSWER)
    assert not v.ok
    assert any(e.startswith("intro") for e in v.errors)


def test_intro_at_limit_ok() -> None:
    assert validate(_products(_p(), intro="ก" * LINE_MAX_TEXT_CHARS), AiRoute.ANSWER).ok


def test_cards_use_only_product_data_not_intro_text() -> None:
    # AI เขียนราคา/ลิงก์ปลอมใน intro -> ตัด intro และการ์ดยังมาจาก products เท่านั้น
    r = _products(_p("A", price=Decimal("100")), intro="ราคา ฿1 ที่ https://evil.example/A")
    v = validate(r, AiRoute.ANSWER)
    assert _types(v) == ["flex"]
    bubble = v.outbound.messages[0]["contents"]["contents"][0]
    assert bubble["footer"]["contents"][0]["action"]["uri"] == "https://shop.example/A"
    assert bubble["body"]["contents"][1]["text"] == "฿100"


# --- ข้อความอิสระของ AI: ลิงก์ / ราคา ---


def _intro_text(v) -> str | None:
    """intro ที่จะส่งจริง (ข้อความ text ก่อน carousel) หรือ None ถ้าถูกตัด"""
    assert v.ok, v.errors
    texts = [m["text"] for m in v.outbound.messages if m["type"] == "text"]
    return texts[0] if texts else None


def test_intro_keeps_product_url() -> None:
    r = _products(_p("A"), intro="ดูได้ที่ https://shop.example/A ค่ะ")
    v = validate(r, AiRoute.ANSWER)
    assert _intro_text(v) == "ดูได้ที่ https://shop.example/A ค่ะ"
    assert v.errors == []


@pytest.mark.parametrize(
    ("intro", "expected"),
    [
        ("ดูได้ที่https://evil.example/xคะ", "ดูได้ที่คะ"),
        ("ดูได้ที่ https://evil.example/x ค่ะ", "ดูได้ที่ ค่ะ"),
        ("ดูที่ http://shop.example/A.", "ดูที่ ."),
        ("ไปที่ www.evil.example/promo ได้เลย", "ไปที่ ได้เลย"),
        ("สั่งที่ shop.example.com/xxx นะคะ", "สั่งที่ นะคะ"),
        ("สั่งที่shop.example.comนะคะ", "สั่งที่นะคะ"),
        # ใกล้เคียงกับลิงก์สินค้าแต่ไม่ตรงทุกตัวอักษร -> ลบ
        ("ดู https://shop.example/AB ค่ะ", "ดู ค่ะ"),
        ("ดู https://shop.example/A?ref=ai ค่ะ", "ดู ค่ะ"),
        ("บรรทัดแรก https://evil.example/x\nบรรทัดสอง", "บรรทัดแรก\nบรรทัดสอง"),
    ],
)
def test_intro_unknown_url_removed_without_failing(intro: str, expected: str) -> None:
    v = validate(_products(_p("A"), intro=intro), AiRoute.ANSWER)
    assert _intro_text(v) == expected
    assert "intro_url_removed" in v.errors
    assert _types(v)[-1] == "flex"


def test_product_url_kept_trailing_punctuation_and_thai() -> None:
    r = _products(_p("A"), _p("B"), intro="A: https://shop.example/A, B: https://shop.example/Bค่ะ")
    assert _intro_text(validate(r, AiRoute.ANSWER)) == r.intro


def test_intro_only_url_is_dropped_carousel_still_sent() -> None:
    v = validate(_products(_p("A"), intro="https://evil.example/x"), AiRoute.ANSWER)
    assert _types(v) == ["flex"]


def test_answer_without_products_strips_url() -> None:
    v = validate(
        AiResult(kind=AiKind.ANSWER, intro="โปรโมชันอยู่ที่ https://evil.example/p ค่ะ"),
        AiRoute.ANSWER,
    )
    assert _intro_text(v) == "โปรโมชันอยู่ที่ ค่ะ"
    assert v.errors == ["intro_url_removed"]


def test_answer_without_products_only_url_fails() -> None:
    v = validate(AiResult(kind=AiKind.ANSWER, intro="https://evil.example/p"), AiRoute.ANSWER)
    assert not v.ok


@pytest.mark.parametrize(
    "intro",
    [
        "ราคา฿12,990ค่ะ",
        "ราคา ฿12,990 ค่ะ",
        "ตัวนี้ 12,990 บาทค่ะ",
        "ตัวนี้12990บาทค่ะ",
        "ตัวนี้ 12,990.- ค่ะ",
        "ราคา 12990 ค่ะ",
        "ราคา12,990.00บาท",
        "A ฿12,990, B ฿5,490 ค่ะ",
    ],
)
def test_intro_matching_price_kept(intro: str) -> None:
    r = _products(_p("A", price=Decimal("12990")), _p("B", price=Decimal("5490.00")), intro=intro)
    v = validate(r, AiRoute.ANSWER)
    assert _intro_text(v) == intro
    assert v.errors == []


@pytest.mark.parametrize(
    "intro",
    [
        "ราคา฿12,900ค่ะ",
        "ตัวนี้ 1,290 บาทค่ะ",
        "ตัวนี้12999บาทค่ะ",
        "ตัวนี้ 9,990.- ค่ะ",
        "ราคา 1299 ค่ะ",
        "A ฿12,990, B ฿5,000 ค่ะ",  # ตรงตัวหนึ่ง ไม่ตรงอีกตัว -> ตัดทั้งข้อความ
    ],
)
def test_intro_price_mismatch_drops_intro_keeps_cards(intro: str) -> None:
    r = _products(_p("A", price=Decimal("12990")), _p("B", price=Decimal("5490")), intro=intro)
    v = validate(r, AiRoute.ANSWER)
    assert v.ok
    assert _types(v) == ["flex"]
    assert v.errors == ["intro_price_mismatch"]


def test_intro_price_with_unpriced_products_drops_intro() -> None:
    v = validate(_products(_p("A"), intro="ราคา ฿100 ค่ะ"), AiRoute.ANSWER)
    assert _types(v) == ["flex"]
    assert "intro_price_mismatch" in v.errors


@pytest.mark.parametrize(
    "intro",
    [
        "ตู้เย็น 9.1 คิว 2 ประตู รุ่นปี 2024 ค่ะ",
        "รุ่น RT38K5032S8 ขนาด 13.2Q ค่ะ",
        "รับประกัน 10 ปี ผ่อน 0% 10 เดือนค่ะ",
    ],
)
def test_non_price_numbers_untouched(intro: str) -> None:
    v = validate(_products(_p("A", price=Decimal("12990")), intro=intro), AiRoute.ANSWER)
    assert _intro_text(v) == intro
    assert v.errors == []


def test_no_products_prices_left_as_is() -> None:
    # ไม่มี products ให้เทียบ -> ส่งราคาตามเดิม (รอคำตอบ: ใครรับผิดชอบราคาผิด / ข้อความกำกับ)
    v = validate(AiResult(kind=AiKind.ANSWER, intro="ค่าติดตั้ง 500 บาทค่ะ"), AiRoute.ANSWER)
    assert _intro_text(v) == "ค่าติดตั้ง 500 บาทค่ะ"
    assert v.errors == []


def test_clarify_question_url_removed() -> None:
    r = AiResult(kind=AiKind.CLARIFY, clarify_question="ดูรุ่นที่https://evil.example/xไหมคะ")
    v = validate(r, AiRoute.CLARIFY)
    assert v.outbound.messages == [replies.text_message("ดูรุ่นที่ไหมคะ")]
    assert v.errors == ["clarify_question_url_removed"]


def test_clarify_question_only_url_fails() -> None:
    r = AiResult(kind=AiKind.CLARIFY, clarify_question="www.evil.example")
    assert not validate(r, AiRoute.CLARIFY).ok


def test_clarify_question_prices_untouched() -> None:
    q = "งบประมาณ 10,000 บาท หรือ 15,000 บาทคะ"
    v = validate(AiResult(kind=AiKind.CLARIFY, clarify_question=q), AiRoute.CLARIFY)
    assert v.outbound.messages == [replies.text_message(q)]


# --- ANSWER ไม่มี products ---


def test_answer_text_only() -> None:
    v = validate(AiResult(kind=AiKind.ANSWER, intro="ร้านเปิด 10 โมงค่ะ"), AiRoute.ANSWER)
    assert v.outbound.messages == [replies.text_message("ร้านเปิด 10 โมงค่ะ")]


@pytest.mark.parametrize("intro", [None, "", "   "])
def test_answer_without_text_fails(intro: str | None) -> None:
    for kind in (AiKind.ANSWER, AiKind.PRODUCTS):
        v = validate(AiResult(kind=kind, intro=intro), AiRoute.ANSWER)
        assert not v.ok


def test_answer_text_too_long_fails() -> None:
    v = validate(
        AiResult(kind=AiKind.ANSWER, intro="a" * (LINE_MAX_TEXT_CHARS + 1)), AiRoute.ANSWER
    )
    assert not v.ok


# --- CLARIFY / OFFER_STAFF / ESCALATE ---


def test_clarify_sends_question() -> None:
    v = validate(AiResult(kind=AiKind.CLARIFY, clarify_question="ต้องการสีอะไรคะ"), AiRoute.CLARIFY)
    assert v.outbound.purpose == "ai_clarify"
    assert v.outbound.messages == [replies.text_message("ต้องการสีอะไรคะ")]


@pytest.mark.parametrize("q", [None, "", " "])
def test_clarify_without_question_fails(q: str | None) -> None:
    assert not validate(AiResult(kind=AiKind.CLARIFY, clarify_question=q), AiRoute.CLARIFY).ok


def test_clarify_question_too_long_fails() -> None:
    r = AiResult(kind=AiKind.CLARIFY, clarify_question="?" * (LINE_MAX_TEXT_CHARS + 1))
    assert not validate(r, AiRoute.CLARIFY).ok


def test_offer_staff_uses_canned_message() -> None:
    v = validate(AiResult(kind=AiKind.CANNOT_ANSWER, intro="AI free text"), AiRoute.OFFER_STAFF)
    assert v.outbound.purpose == "ai_cannot_answer"
    assert v.outbound.messages == [replies.cannot_answer_message()]


def test_escalate_route_is_not_validated() -> None:
    with pytest.raises(ValueError):
        validate(AiResult(kind=AiKind.ANSWER, intro="x"), AiRoute.ESCALATE)


# --- prefix / จำนวนข้อความ ---


def test_prefix_texts_come_first() -> None:
    v = validate(_products(_p()), AiRoute.ANSWER, prefix_texts=[replies.HANDOFF_TIMED_OUT_PREFIX])
    assert v.outbound.messages[0] == replies.text_message(replies.HANDOFF_TIMED_OUT_PREFIX)
    assert _types(v) == ["text", "text", "flex"]


def test_too_many_messages_fails() -> None:
    v = validate(_products(_p()), AiRoute.ANSWER, prefix_texts=["a", "b", "c", "d"])
    assert not v.ok
    assert any(e.startswith("messages") for e in v.errors)
    assert validate(_products(_p()), AiRoute.ANSWER, prefix_texts=["a", "b", "c"]).ok


def test_blank_or_long_prefix_fails() -> None:
    r = AiResult(kind=AiKind.ANSWER, intro="x")
    assert not validate(r, AiRoute.ANSWER, prefix_texts=[" "]).ok
    assert not validate(r, AiRoute.ANSWER, prefix_texts=["a" * (LINE_MAX_TEXT_CHARS + 1)]).ok


# --- on_validate_fail / reset ---


def test_validate_fail_first_retry_then_escalate() -> None:
    escalate, c1 = on_validate_fail(Counters())
    assert escalate is False and c1.validate_fail_streak == 1
    escalate, c2 = on_validate_fail(c1)
    assert escalate is True and c2.validate_fail_streak == 0
    assert c1.validate_fail_streak == 1  # immutable


def test_success_resets_streak() -> None:
    c = Counters(validate_fail_streak=1, clarify_streak=1)
    r = reset_validate_streak(c)
    assert r.validate_fail_streak == 0 and r.clarify_streak == 1
    escalate, _ = on_validate_fail(r)
    assert escalate is False  # ไม่ติดกันแล้ว
